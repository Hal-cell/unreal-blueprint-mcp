// Copyright (c) 2026 Hal Xu. License: TBD.

#include "TCPServer.h"

#include "Common/TcpSocketBuilder.h"
#include "Sockets.h"
#include "SocketSubsystem.h"
#include "Interfaces/IPv4/IPv4Address.h"
#include "Interfaces/IPv4/IPv4Endpoint.h"
#include "Logging/LogMacros.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Misc/DateTime.h"
#include "Misc/Timespan.h"

// Spike B1: game-thread marshaling + asset creation
#include "Async/Async.h"
#include "Async/Future.h"
#include "Modules/ModuleManager.h"
#include "Factories/BlueprintFactory.h"
#include "AssetToolsModule.h"
#include "IAssetTools.h"
#include "Engine/Blueprint.h"
#include "EditorAssetLibrary.h"

// Parent class types (Spike B1 whitelist)
#include "GameFramework/Actor.h"
#include "GameFramework/Pawn.h"
#include "GameFramework/Character.h"
#include "Components/ActorComponent.h"

// Spike B2: node creation
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "K2Node_CallFunction.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "UObject/UObjectIterator.h"
#include "Misc/Guid.h"

// Spike B3: pin default values
#include "EdGraphSchema_K2.h"

// Spike B4: connect pins + well-known event anchors
#include "K2Node_Event.h"

// Spike B5: compile blueprint
#include "Kismet2/KismetEditorUtilities.h"

// Spike B6: spawn actor into current level
#include "Subsystems/EditorActorSubsystem.h"
#include "Editor.h"   // GEditor

// Spike B7: add_component (SCS)
#include "Engine/SimpleConstructionScript.h"
#include "Engine/SCS_Node.h"
#include "Components/ActorComponent.h"
#include "Components/BoxComponent.h"
#include "Components/SphereComponent.h"
#include "Components/CapsuleComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Camera/CameraComponent.h"
#include "Components/PointLightComponent.h"
#include "Components/SpotLightComponent.h"
#include "Components/AudioComponent.h"

// Spike B8: add_custom_event
#include "K2Node_CustomEvent.h"

// Spike B9: add_variable (+ TimerHandle struct)
#include "Engine/EngineTypes.h"  // FTimerHandle

// Spike B10: variable get/set node refs
#include "K2Node_VariableGet.h"
#include "K2Node_VariableSet.h"

// v2 get_blueprint: snapshot serialization
#include "Policies/CondensedJsonPrintPolicy.h"

// v3 flow control + casting
#include "K2Node_IfThenElse.h"
#include "K2Node_DynamicCast.h"
#include "GameFramework/PlayerController.h"
#include "GameFramework/PlayerState.h"
#include "GameFramework/GameModeBase.h"
#include "GameFramework/HUD.h"
#include "Camera/PlayerCameraManager.h"

// v4 — macros, self, input, struct defaults, destructive ops
#include "K2Node_MacroInstance.h"
#include "K2Node_Self.h"
#include "K2Node_InputKey.h"
#include "InputCoreTypes.h"

// v5 — Enhanced Input + user functions + BP-to-BP
#include "InputAction.h"
#include "InputMappingContext.h"
#include "EnhancedInputComponent.h"
#include "K2Node_EnhancedInputAction.h"
#include "Kismet2/KismetEditorUtilities.h"  // already included earlier but harmless

DEFINE_LOG_CATEGORY_STATIC(LogBlueprintMCP_TCP, Log, All);

namespace
{
    // Forward declarations: helpers defined later in this anon namespace that
    // are referenced from "OnGameThread" functions defined earlier in source.
    // (C++ requires either forward decl or definition-before-use.)
    FString FormatStructDefault(UScriptStruct* StructType, const FString& UserInput);
    bool IsSupportedStructForDefault(UScriptStruct* StructType);

    constexpr int32 kReceiveBufferSize = 8192;
    constexpr int32 kGameThreadTimeoutSeconds = 10;

    /** Map a user-friendly parent-class name to a UClass*. Spike B1 whitelist. */
    UClass* ResolveParentClass(const FString& Name)
    {
        if (Name.Equals(TEXT("Actor"), ESearchCase::IgnoreCase))          return AActor::StaticClass();
        if (Name.Equals(TEXT("Pawn"), ESearchCase::IgnoreCase))           return APawn::StaticClass();
        if (Name.Equals(TEXT("Character"), ESearchCase::IgnoreCase))      return ACharacter::StaticClass();
        if (Name.Equals(TEXT("Object"), ESearchCase::IgnoreCase))         return UObject::StaticClass();
        if (Name.Equals(TEXT("ActorComponent"), ESearchCase::IgnoreCase)) return UActorComponent::StaticClass();
        return nullptr;
    }

    // Note: UE's built-in EscapeJsonString (Serialization/JsonWriter.h) **also adds
    // the surrounding quotes** ("Also adds the quotes" per its docstring) —
    // so format strings must use %s, NOT \"%s\". Discovered the hard way in B1.

    /** Build an error response JSON line. */
    FString JsonError(const FString& Command, const FString& Error, const FString& Detail = FString())
    {
        if (Detail.IsEmpty())
        {
            return FString::Printf(
                TEXT("{\"ok\":false,\"command\":%s,\"error\":%s}\n"),
                *EscapeJsonString(Command), *EscapeJsonString(Error));
        }
        return FString::Printf(
            TEXT("{\"ok\":false,\"command\":%s,\"error\":%s,\"detail\":%s}\n"),
            *EscapeJsonString(Command), *EscapeJsonString(Error), *EscapeJsonString(Detail));
    }

    // ----- Spike B2 helpers -----

    /** Resolve a bare function short-name to (ClassName, FunctionName). v0..v5 whitelist. */
    bool ResolveFunctionShortName(const FString& ShortName, FString& OutClassName, FString& OutFuncName)
    {
        // Whitelist — extend as we go. Add new entries to BOTH this map and the Python docstring.
        static const TMap<FString, TPair<FString, FString>> kMap = {
            // --- v0+v1 ---
            { TEXT("PrintString"),                    { TEXT("KismetSystemLibrary"), TEXT("PrintString") } },
            { TEXT("Delay"),                          { TEXT("KismetSystemLibrary"), TEXT("Delay") } },
            { TEXT("SetTimerByEvent"),                { TEXT("KismetSystemLibrary"), TEXT("K2_SetTimerDelegate") } },
            { TEXT("ClearAndInvalidateTimerByHandle"),{ TEXT("KismetSystemLibrary"), TEXT("K2_ClearAndInvalidateTimerHandle") } },
            // --- v5 math (KismetMathLibrary) ---
            { TEXT("MakeVector"),                     { TEXT("KismetMathLibrary"), TEXT("MakeVector") } },
            { TEXT("BreakVector"),                    { TEXT("KismetMathLibrary"), TEXT("BreakVector") } },
            { TEXT("MakeRotator"),                    { TEXT("KismetMathLibrary"), TEXT("MakeRotator") } },
            { TEXT("BreakRotator"),                   { TEXT("KismetMathLibrary"), TEXT("BreakRotator") } },
            { TEXT("VectorLength"),                   { TEXT("KismetMathLibrary"), TEXT("VSize") } },
            { TEXT("VectorDistance"),                 { TEXT("KismetMathLibrary"), TEXT("Vector_Distance") } },
            { TEXT("NormalizeVector"),                { TEXT("KismetMathLibrary"), TEXT("Normal") } },
            { TEXT("GetForwardVector"),               { TEXT("KismetMathLibrary"), TEXT("GetForwardVector") } },
            { TEXT("GetRightVector"),                 { TEXT("KismetMathLibrary"), TEXT("GetRightVector") } },
            { TEXT("GetUpVector"),                    { TEXT("KismetMathLibrary"), TEXT("GetUpVector") } },
            { TEXT("VectorLerp"),                     { TEXT("KismetMathLibrary"), TEXT("VLerp") } },
            { TEXT("RotatorLerp"),                    { TEXT("KismetMathLibrary"), TEXT("RLerp") } },
            { TEXT("FloatLerp"),                      { TEXT("KismetMathLibrary"), TEXT("Lerp") } },
            { TEXT("VInterpTo"),                      { TEXT("KismetMathLibrary"), TEXT("VInterpTo") } },
            { TEXT("RInterpTo"),                      { TEXT("KismetMathLibrary"), TEXT("RInterpTo") } },
            { TEXT("FInterpTo"),                      { TEXT("KismetMathLibrary"), TEXT("FInterpTo") } },
            { TEXT("RandomFloat"),                    { TEXT("KismetMathLibrary"), TEXT("RandomFloat") } },
            { TEXT("RandomFloatInRange"),             { TEXT("KismetMathLibrary"), TEXT("RandomFloatInRange") } },
            { TEXT("RandomInt"),                      { TEXT("KismetMathLibrary"), TEXT("RandomInteger") } },
            { TEXT("Abs"),                            { TEXT("KismetMathLibrary"), TEXT("Abs") } },
            { TEXT("Min"),                            { TEXT("KismetMathLibrary"), TEXT("FMin") } },
            { TEXT("Max"),                            { TEXT("KismetMathLibrary"), TEXT("FMax") } },
            // --- v5 system ---
            { TEXT("IsValid"),                        { TEXT("KismetSystemLibrary"), TEXT("IsValid") } },
            { TEXT("GetDisplayName"),                 { TEXT("KismetSystemLibrary"), TEXT("GetDisplayName") } },
            { TEXT("PrintText"),                      { TEXT("KismetSystemLibrary"), TEXT("PrintText") } },
            { TEXT("GetGameTimeInSeconds"),           { TEXT("KismetSystemLibrary"), TEXT("GetGameTimeInSeconds") } },
            // --- v5 gameplay (GameplayStatics) ---
            { TEXT("GetPlayerPawn"),                  { TEXT("GameplayStatics"),    TEXT("GetPlayerPawn") } },
            { TEXT("GetPlayerController"),            { TEXT("GameplayStatics"),    TEXT("GetPlayerController") } },
            { TEXT("GetPlayerCharacter"),             { TEXT("GameplayStatics"),    TEXT("GetPlayerCharacter") } },
            { TEXT("GetGameMode"),                    { TEXT("GameplayStatics"),    TEXT("GetGameMode") } },
            { TEXT("GetWorldDeltaSeconds"),           { TEXT("GameplayStatics"),    TEXT("GetWorldDeltaSeconds") } },
            { TEXT("ApplyDamage"),                    { TEXT("GameplayStatics"),    TEXT("ApplyDamage") } },
            { TEXT("OpenLevel"),                      { TEXT("GameplayStatics"),    TEXT("OpenLevel") } },
            // --- v5 array (KismetArrayLibrary) ---
            { TEXT("ArrayLength"),                    { TEXT("KismetArrayLibrary"), TEXT("Array_Length") } },
            { TEXT("ArrayAdd"),                       { TEXT("KismetArrayLibrary"), TEXT("Array_Add") } },
            { TEXT("ArrayGet"),                       { TEXT("KismetArrayLibrary"), TEXT("Array_Get") } },
            { TEXT("ArraySet"),                       { TEXT("KismetArrayLibrary"), TEXT("Array_Set") } },
            { TEXT("ArrayClear"),                     { TEXT("KismetArrayLibrary"), TEXT("Array_Clear") } },
            { TEXT("ArrayContains"),                  { TEXT("KismetArrayLibrary"), TEXT("Array_Contains") } },
            { TEXT("ArrayRemove"),                    { TEXT("KismetArrayLibrary"), TEXT("Array_RemoveItem") } },
        };
        if (const TPair<FString, FString>* Found = kMap.Find(ShortName))
        {
            OutClassName = Found->Key;
            OutFuncName = Found->Value;
            return true;
        }
        return false;
    }

    // ----- Spike B7 helpers -----

    /** Map a user-friendly component name to a UClass*. v1 whitelist + qualified fallback. */
    UClass* ResolveComponentClass(const FString& Name)
    {
        // Short-name whitelist
        if (Name.Equals(TEXT("BoxCollision"), ESearchCase::IgnoreCase) ||
            Name.Equals(TEXT("Box"), ESearchCase::IgnoreCase))         return UBoxComponent::StaticClass();
        if (Name.Equals(TEXT("SphereCollision"), ESearchCase::IgnoreCase) ||
            Name.Equals(TEXT("Sphere"), ESearchCase::IgnoreCase))      return USphereComponent::StaticClass();
        if (Name.Equals(TEXT("CapsuleCollision"), ESearchCase::IgnoreCase) ||
            Name.Equals(TEXT("Capsule"), ESearchCase::IgnoreCase))     return UCapsuleComponent::StaticClass();
        if (Name.Equals(TEXT("StaticMesh"), ESearchCase::IgnoreCase))  return UStaticMeshComponent::StaticClass();
        if (Name.Equals(TEXT("Camera"), ESearchCase::IgnoreCase))      return UCameraComponent::StaticClass();
        if (Name.Equals(TEXT("PointLight"), ESearchCase::IgnoreCase))  return UPointLightComponent::StaticClass();
        if (Name.Equals(TEXT("SpotLight"), ESearchCase::IgnoreCase))   return USpotLightComponent::StaticClass();
        if (Name.Equals(TEXT("Audio"), ESearchCase::IgnoreCase))       return UAudioComponent::StaticClass();

        // Qualified fallback: must be an ActorComponent subclass
        if (UClass* Found = FindFirstObject<UClass>(*Name, EFindFirstObjectOptions::NativeFirst))
        {
            if (Found->IsChildOf(UActorComponent::StaticClass()))
            {
                return Found;
            }
        }
        return nullptr;
    }

    // ----- Spike B9 helpers -----

    /**
     * Resolve a user-friendly key name to UE's FKey, applying common aliases.
     * v5.0.1: LLMs / humans say "Space" but UE wants "SpaceBar". Same for Esc/Escape etc.
     */
    FKey ResolveFKeyWithAliases(const FString& Name)
    {
        // Lowercase alias map for common day-to-day key names → UE's canonical FName
        static const TMap<FString, FString> kAliases = {
            { TEXT("space"),      TEXT("SpaceBar") },
            { TEXT("esc"),        TEXT("Escape") },
            { TEXT("return"),     TEXT("Enter") },
            { TEXT("ctrl"),       TEXT("LeftControl") },     // ambiguous; default to Left
            { TEXT("control"),    TEXT("LeftControl") },
            { TEXT("alt"),        TEXT("LeftAlt") },
            { TEXT("shift"),      TEXT("LeftShift") },
            { TEXT("cmd"),        TEXT("LeftCommand") },
            { TEXT("command"),    TEXT("LeftCommand") },
            { TEXT("win"),        TEXT("LeftCommand") },
            { TEXT("delete"),     TEXT("Delete") },           // UE accepts this; no-op
            { TEXT("backspace"),  TEXT("BackSpace") },
        };

        const FString Lower = Name.ToLower();
        const FString* Mapped = kAliases.Find(Lower);
        const FString& Resolved = Mapped ? *Mapped : Name;
        return FKey(*Resolved);
    }

    /** Build an FEdGraphPinType for a user-friendly variable type key. v1+v5 whitelist. */
    bool ResolveVariablePinType(const FString& TypeKey, FEdGraphPinType& OutType)
    {
        OutType = FEdGraphPinType();

        // v5: array types — "int[]" / "float[]" / "string[]" / "bool[]" / "name[]"
        FString BaseType = TypeKey;
        if (BaseType.EndsWith(TEXT("[]")))
        {
            BaseType = BaseType.LeftChop(2);  // strip "[]"
            OutType.ContainerType = EPinContainerType::Array;
        }

        if (BaseType.Equals(TEXT("bool"), ESearchCase::IgnoreCase))
        {
            OutType.PinCategory = UEdGraphSchema_K2::PC_Boolean;
            return true;
        }
        if (BaseType.Equals(TEXT("int"), ESearchCase::IgnoreCase) ||
            BaseType.Equals(TEXT("integer"), ESearchCase::IgnoreCase))
        {
            OutType.PinCategory = UEdGraphSchema_K2::PC_Int;
            return true;
        }
        if (BaseType.Equals(TEXT("float"), ESearchCase::IgnoreCase) ||
            BaseType.Equals(TEXT("double"), ESearchCase::IgnoreCase) ||
            BaseType.Equals(TEXT("real"), ESearchCase::IgnoreCase))
        {
            OutType.PinCategory = UEdGraphSchema_K2::PC_Real;
            OutType.PinSubCategory = UEdGraphSchema_K2::PC_Double;
            return true;
        }
        if (BaseType.Equals(TEXT("string"), ESearchCase::IgnoreCase))
        {
            OutType.PinCategory = UEdGraphSchema_K2::PC_String;
            return true;
        }
        if (BaseType.Equals(TEXT("name"), ESearchCase::IgnoreCase))
        {
            OutType.PinCategory = UEdGraphSchema_K2::PC_Name;
            return true;
        }
        if (BaseType.Equals(TEXT("text"), ESearchCase::IgnoreCase))
        {
            OutType.PinCategory = UEdGraphSchema_K2::PC_Text;
            return true;
        }
        // ⭐ TimerHandle — for B9 v1 demo (no array form supported)
        if (BaseType.Equals(TEXT("TimerHandle"), ESearchCase::IgnoreCase))
        {
            if (OutType.ContainerType == EPinContainerType::Array) return false;  // no TimerHandle[]
            OutType.PinCategory = UEdGraphSchema_K2::PC_Struct;
            OutType.PinSubCategoryObject = TBaseStructure<FTimerHandle>::Get();
            return true;
        }
        return false;
    }

    /** Find a loaded UClass by bare name (e.g., "KismetSystemLibrary" → UKismetSystemLibrary). */
    UClass* FindUClassByName(const FString& ClassName)
    {
        // EFindFirstObjectOptions::NativeFirst — prefer engine-native classes over assets named similarly.
        UClass* Found = FindFirstObject<UClass>(*ClassName, EFindFirstObjectOptions::NativeFirst);
        return Found;
    }

    /** Build the JSON array string describing a node's pins. */
    FString BuildPinsJsonArray(const UEdGraphNode* Node)
    {
        TArray<FString> PinJsonItems;
        for (const UEdGraphPin* Pin : Node->Pins)
        {
            const FString PinName = Pin->PinName.ToString();
            const FString Direction = (Pin->Direction == EGPD_Input) ? TEXT("input") : TEXT("output");
            const FString TypeCategory = Pin->PinType.PinCategory.ToString();
            PinJsonItems.Add(FString::Printf(
                TEXT("{\"name\":%s,\"direction\":\"%s\",\"type\":%s}"),
                *EscapeJsonString(PinName),
                *Direction,
                *EscapeJsonString(TypeCategory)));
        }
        return TEXT("[") + FString::Join(PinJsonItems, TEXT(",")) + TEXT("]");
    }

    /**
     * Add a node to a Blueprint's EventGraph. MUST run on the game thread.
     * Returns a complete JSON response line (with trailing \n).
     */
    FString AddNodeOnGameThread(
        const FString& BlueprintPath,
        const FString& NodeType,
        const FString& AnchorName,
        int32 PosX,
        int32 PosY)
    {
        check(IsInGameThread());

        // 1. Load Blueprint
        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr)
        {
            return JsonError(TEXT("add_node"), TEXT("blueprint_not_found"), BlueprintPath);
        }

        // 2. Get EventGraph (first ubergraph page is "EventGraph" by convention)
        if (Blueprint->UbergraphPages.Num() == 0)
        {
            return JsonError(TEXT("add_node"), TEXT("no_event_graph"), BlueprintPath);
        }
        UEdGraph* EventGraph = Blueprint->UbergraphPages[0];

        // 3. Check anchor_name uniqueness within this graph
        for (const UEdGraphNode* Existing : EventGraph->Nodes)
        {
            if (Existing != nullptr && Existing->NodeComment.Equals(AnchorName, ESearchCase::CaseSensitive))
            {
                return JsonError(TEXT("add_node"), TEXT("anchor_name_exists"), AnchorName);
            }
        }

        // 4. Parse node_type — format "<K2NodeClass>:<param>"
        FString NodeClass, NodeParam;
        if (!NodeType.Split(TEXT(":"), &NodeClass, &NodeParam))
        {
            return JsonError(TEXT("add_node"), TEXT("invalid_node_type"), NodeType);
        }

        // 5. Branch on node class (v0 only K2Node_CallFunction)
        if (!NodeClass.Equals(TEXT("K2Node_CallFunction"), ESearchCase::IgnoreCase))
        {
            return JsonError(TEXT("add_node"), TEXT("unsupported_node_class"), NodeClass);
        }

        // 6. Resolve function reference: try "Class.Function" qualified form first, else short-name whitelist
        FString OwningClassName, FunctionName;
        if (!NodeParam.Split(TEXT("."), &OwningClassName, &FunctionName))
        {
            // Bare name — try whitelist
            if (!ResolveFunctionShortName(NodeParam, OwningClassName, FunctionName))
            {
                return JsonError(TEXT("add_node"), TEXT("unknown_function"), NodeParam);
            }
        }

        UClass* OwningClass = FindUClassByName(OwningClassName);
        if (OwningClass == nullptr)
        {
            return JsonError(TEXT("add_node"), TEXT("class_not_found"), OwningClassName);
        }
        UFunction* TargetFunc = OwningClass->FindFunctionByName(FName(*FunctionName));
        if (TargetFunc == nullptr)
        {
            return JsonError(TEXT("add_node"), TEXT("function_not_found"),
                FString::Printf(TEXT("%s.%s"), *OwningClassName, *FunctionName));
        }

        // 7. Spawn K2Node_CallFunction in the EventGraph
        UK2Node_CallFunction* NewNode = NewObject<UK2Node_CallFunction>(EventGraph);
        NewNode->SetFlags(RF_Transactional);
        NewNode->FunctionReference.SetExternalMember(TargetFunc->GetFName(), OwningClass);
        NewNode->NodePosX = PosX;
        NewNode->NodePosY = PosY;
        NewNode->NodeComment = AnchorName;          // anchor lives here, visible in editor
        NewNode->bCommentBubbleVisible = true;

        EventGraph->AddNode(NewNode, /*bFromUI*/ false, /*bSelectNewNode*/ false);
        NewNode->CreateNewGuid();
        NewNode->PostPlacedNewNode();
        NewNode->AllocateDefaultPins();

        // 8. Mark BP dirty + save
        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);
        if (!bSaved)
        {
            UE_LOG(LogBlueprintMCP_TCP, Warning, TEXT("add_node: node added but save failed (%s)"), *BlueprintPath);
        }

        // 9. Build response
        const FString PinsJson = BuildPinsJsonArray(NewNode);
        const FString GuidStr = NewNode->NodeGuid.ToString(EGuidFormats::DigitsWithHyphens);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"add_node\",\"anchor_name\":%s,\"node_guid\":%s,\"node_type\":\"K2Node_CallFunction\",\"function\":%s,\"owning_class\":%s,\"pins\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(AnchorName),
            *EscapeJsonString(GuidStr),
            *EscapeJsonString(FunctionName),
            *EscapeJsonString(OwningClassName),
            *PinsJson,
            bSaved ? TEXT("true") : TEXT("false"));
    }

    // ----- Spike B3+ helpers -----

    /**
     * Find a node in an EdGraph by anchor name.
     *
     * Lookup order:
     *   1. NodeComment exact match (case-sensitive) — for nodes added via add_node
     *   2. Well-known event short-name fallback — for default events that
     *      UBlueprintFactory auto-added (no NodeComment). E.g., "begin_play"
     *      maps to the K2Node_Event whose EventReference is `ReceiveBeginPlay`.
     *
     * Returns nullptr if not found.
     */
    UEdGraphNode* FindNodeByAnchor(UEdGraph* Graph, const FString& AnchorName)
    {
        // 1. NodeComment match (primary)
        for (UEdGraphNode* Node : Graph->Nodes)
        {
            if (Node != nullptr && Node->NodeComment.Equals(AnchorName, ESearchCase::CaseSensitive))
            {
                return Node;
            }
        }

        // 2. Well-known event short-name fallback (added Spike B4)
        static const TMap<FString, FName> kWellKnownEvents = {
            { TEXT("begin_play"),          FName(TEXT("ReceiveBeginPlay")) },
            { TEXT("tick"),                FName(TEXT("ReceiveTick")) },
            { TEXT("end_play"),            FName(TEXT("ReceiveEndPlay")) },
            { TEXT("actor_begin_overlap"), FName(TEXT("ReceiveActorBeginOverlap")) },
            { TEXT("actor_end_overlap"),   FName(TEXT("ReceiveActorEndOverlap")) },
            { TEXT("hit"),                 FName(TEXT("ReceiveHit")) },
            { TEXT("destroyed"),           FName(TEXT("ReceiveDestroyed")) },
        };

        if (const FName* EventFunc = kWellKnownEvents.Find(AnchorName.ToLower()))
        {
            for (UEdGraphNode* Node : Graph->Nodes)
            {
                if (UK2Node_Event* EventNode = Cast<UK2Node_Event>(Node))
                {
                    if (EventNode->EventReference.GetMemberName() == *EventFunc)
                    {
                        return EventNode;
                    }
                }
            }
        }

        return nullptr;
    }

    /**
     * Like FindNodeByAnchor, but for well-known event short names (begin_play, tick,
     * actor_end_overlap, ...) **auto-spawns** the K2Node_Event if it doesn't exist yet.
     *
     * Why: UBlueprintFactory only auto-spawns 3 default events (BeginPlay / Tick /
     * ActorBeginOverlap). The other 4 well-known short names refer to events that
     * MAY override on the parent class but have no spawned node — without spawn-on-
     * demand, those short names would return anchor_not_found every time.
     *
     * Use this from tools that REFERENCE existing nodes (connect_pins, set_pin_default).
     * Do NOT use this from uniqueness checks in add_* tools (which want strict lookup).
     */
    UEdGraphNode* FindOrSpawnNodeByAnchor(UEdGraph* Graph, const FString& AnchorName)
    {
        // Strict lookup first — preserves backward compat + handles user-named anchors
        if (UEdGraphNode* Found = FindNodeByAnchor(Graph, AnchorName))
        {
            return Found;
        }

        // Not found — is it a well-known event short name?
        static const TMap<FString, FName> kWellKnownEvents = {
            { TEXT("begin_play"),          FName(TEXT("ReceiveBeginPlay")) },
            { TEXT("tick"),                FName(TEXT("ReceiveTick")) },
            { TEXT("end_play"),            FName(TEXT("ReceiveEndPlay")) },
            { TEXT("actor_begin_overlap"), FName(TEXT("ReceiveActorBeginOverlap")) },
            { TEXT("actor_end_overlap"),   FName(TEXT("ReceiveActorEndOverlap")) },
            { TEXT("hit"),                 FName(TEXT("ReceiveHit")) },
            { TEXT("destroyed"),           FName(TEXT("ReceiveDestroyed")) },
        };

        const FName* EventFunc = kWellKnownEvents.Find(AnchorName.ToLower());
        if (EventFunc == nullptr)
        {
            return nullptr;
        }

        // Get the owning BP + parent class
        UBlueprint* Blueprint = FBlueprintEditorUtils::FindBlueprintForGraph(Graph);
        if (Blueprint == nullptr || Blueprint->ParentClass == nullptr)
        {
            return nullptr;
        }

        // Verify the parent class actually has this overridable event
        UFunction* EventFunction = FindUField<UFunction>(Blueprint->ParentClass, *EventFunc);
        if (EventFunction == nullptr)
        {
            // BP's parent class doesn't have this event (e.g., ReceiveHit on a
            // non-physics actor). Caller will report anchor_not_found.
            return nullptr;
        }

        // Spawn the event node. Position: stack down from any existing events
        // (so the new one doesn't overlap visually).
        int32 NewPosY = 0;
        for (const UEdGraphNode* ExistingNode : Graph->Nodes)
        {
            if (Cast<const UK2Node_Event>(ExistingNode) && ExistingNode->NodePosY >= NewPosY)
            {
                NewPosY = ExistingNode->NodePosY + 200;
            }
        }

        UK2Node_Event* NewEventNode = NewObject<UK2Node_Event>(Graph);
        NewEventNode->SetFlags(RF_Transactional);
        NewEventNode->EventReference.SetExternalMember(*EventFunc, Blueprint->ParentClass);
        NewEventNode->bOverrideFunction = true;
        NewEventNode->NodePosX = -300;
        NewEventNode->NodePosY = NewPosY;

        Graph->AddNode(NewEventNode, /*bFromUI*/ false, /*bSelectNewNode*/ false);
        NewEventNode->CreateNewGuid();
        NewEventNode->PostPlacedNewNode();
        NewEventNode->AllocateDefaultPins();

        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);

        UE_LOG(LogBlueprintMCP_TCP, Log,
            TEXT("FindOrSpawnNodeByAnchor: auto-spawned %s for anchor '%s' in %s"),
            *EventFunc->ToString(), *AnchorName, *Blueprint->GetName());

        return NewEventNode;
    }

    /**
     * Resolve a pin_ref string ("anchor.pin") into a UEdGraphPin*.
     * On failure, sets OutErrorJson to a JSON error line and returns nullptr.
     * Used by both set_pin_default (B3) and connect_pins (B4).
     *
     * **Auto-spawn behavior:** for well-known event short names that don't have
     * a node yet (e.g., "actor_end_overlap" in a fresh BP), this auto-spawns
     * the K2Node_Event for them. See FindOrSpawnNodeByAnchor.
     */
    UEdGraphPin* ResolvePinRef(
        UEdGraph* Graph,
        const FString& PinRef,
        const FString& Command,
        FString& OutErrorJson)
    {
        FString AnchorName, PinName;
        if (!PinRef.Split(TEXT("."), &AnchorName, &PinName))
        {
            OutErrorJson = JsonError(Command, TEXT("invalid_pin_ref"),
                FString::Printf(TEXT("%s (expected anchor.pin)"), *PinRef));
            return nullptr;
        }

        UEdGraphNode* Node = FindOrSpawnNodeByAnchor(Graph, AnchorName);
        if (Node == nullptr)
        {
            OutErrorJson = JsonError(Command, TEXT("anchor_not_found"), AnchorName);
            return nullptr;
        }

        UEdGraphPin* Pin = Node->FindPin(FName(*PinName));
        if (Pin == nullptr)
        {
            OutErrorJson = JsonError(Command, TEXT("pin_not_found"),
                FString::Printf(TEXT("%s on %s"), *PinName, *AnchorName));
            return nullptr;
        }
        return Pin;
    }

    /** True if this pin type accepts a string-encoded default value in v0. */
    bool IsSupportedPinTypeForDefault(const FName& Category)
    {
        static const TSet<FName> kSupported = {
            UEdGraphSchema_K2::PC_String,
            UEdGraphSchema_K2::PC_Name,
            UEdGraphSchema_K2::PC_Text,
            UEdGraphSchema_K2::PC_Int,
            UEdGraphSchema_K2::PC_Int64,
            UEdGraphSchema_K2::PC_Real,       // float and double in UE5
            UEdGraphSchema_K2::PC_Boolean,
            UEdGraphSchema_K2::PC_Byte,       // also covers enums (subcat carries UEnum)
        };
        return kSupported.Contains(Category);
    }

    /**
     * Set an input pin's default value. MUST run on the game thread.
     * Returns a complete JSON response line (with trailing \n).
     */
    FString SetPinDefaultOnGameThread(
        const FString& BlueprintPath,
        const FString& PinRef,        // "<anchor_name>.<pin_name>"
        const FString& Value)
    {
        check(IsInGameThread());

        // 1. Load Blueprint
        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr)
        {
            return JsonError(TEXT("set_pin_default"), TEXT("blueprint_not_found"), BlueprintPath);
        }

        // 2. Get EventGraph
        if (Blueprint->UbergraphPages.Num() == 0)
        {
            return JsonError(TEXT("set_pin_default"), TEXT("no_event_graph"), BlueprintPath);
        }
        UEdGraph* EventGraph = Blueprint->UbergraphPages[0];

        // 3. Parse pin_ref: "anchor.pin"
        FString AnchorName, PinName;
        if (!PinRef.Split(TEXT("."), &AnchorName, &PinName))
        {
            return JsonError(TEXT("set_pin_default"), TEXT("invalid_pin_ref"),
                FString::Printf(TEXT("%s (expected anchor.pin)"), *PinRef));
        }

        // 4. Find target node (auto-spawns well-known events if missing)
        UEdGraphNode* TargetNode = FindOrSpawnNodeByAnchor(EventGraph, AnchorName);
        if (TargetNode == nullptr)
        {
            return JsonError(TEXT("set_pin_default"), TEXT("anchor_not_found"), AnchorName);
        }

        // 5. Find target pin
        UEdGraphPin* TargetPin = TargetNode->FindPin(FName(*PinName));
        if (TargetPin == nullptr)
        {
            return JsonError(TEXT("set_pin_default"), TEXT("pin_not_found"),
                FString::Printf(TEXT("%s on %s"), *PinName, *AnchorName));
        }

        // 6. Validate: must be an input pin, not exec, supported type
        if (TargetPin->Direction != EGPD_Input)
        {
            return JsonError(TEXT("set_pin_default"), TEXT("pin_not_input"), PinRef);
        }
        if (TargetPin->PinType.PinCategory == UEdGraphSchema_K2::PC_Exec)
        {
            return JsonError(TEXT("set_pin_default"), TEXT("exec_pin_no_default"), PinRef);
        }
        // v1 primitive types OR v4 struct types
        const FName Category = TargetPin->PinType.PinCategory;
        UScriptStruct* StructType = nullptr;
        FString ValueToSet = Value;

        if (Category == UEdGraphSchema_K2::PC_Struct)
        {
            StructType = Cast<UScriptStruct>(TargetPin->PinType.PinSubCategoryObject.Get());
            if (!IsSupportedStructForDefault(StructType))
            {
                return JsonError(TEXT("set_pin_default"), TEXT("unsupported_struct_type"),
                    StructType ? StructType->GetName() : TEXT("unknown"));
            }
            // v4: reformat user input into UE-canonical struct text
            ValueToSet = FormatStructDefault(StructType, Value);
        }
        else if (!IsSupportedPinTypeForDefault(Category))
        {
            return JsonError(TEXT("set_pin_default"), TEXT("unsupported_pin_type"), Category.ToString());
        }

        // 7. Set via schema (triggers type coercion + node callbacks; correct path)
        const UEdGraphSchema_K2* Schema = Cast<UEdGraphSchema_K2>(EventGraph->GetSchema());
        if (Schema == nullptr)
        {
            return JsonError(TEXT("set_pin_default"), TEXT("schema_not_k2"), BlueprintPath);
        }
        Schema->TrySetDefaultValue(*TargetPin, ValueToSet, /*bMarkAsModified*/ true);

        // 8. Mark BP modified + save
        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);
        if (!bSaved)
        {
            UE_LOG(LogBlueprintMCP_TCP, Warning,
                TEXT("set_pin_default: value set but save failed (%s)"), *BlueprintPath);
        }

        // 9. Build response — report what UE actually stored (may differ after coercion)
        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"set_pin_default\",\"anchor_name\":%s,\"pin_name\":%s,\"value\":%s,\"pin_type\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(AnchorName),
            *EscapeJsonString(PinName),
            *EscapeJsonString(TargetPin->DefaultValue),
            *EscapeJsonString(TargetPin->PinType.PinCategory.ToString()),
            bSaved ? TEXT("true") : TEXT("false"));
    }

    // ===== Spike B7 — add_component =====

    FString AddComponentOnGameThread(
        const FString& BlueprintPath,
        const FString& ComponentClassStr,
        const FString& ComponentName)
    {
        check(IsInGameThread());

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr)
        {
            return JsonError(TEXT("add_component"), TEXT("blueprint_not_found"), BlueprintPath);
        }
        if (Blueprint->ParentClass == nullptr || !Blueprint->ParentClass->IsChildOf(AActor::StaticClass()))
        {
            return JsonError(TEXT("add_component"), TEXT("parent_not_actor"),
                TEXT("Components only work in Actor-derived Blueprints"));
        }

        UClass* ComponentClass = ResolveComponentClass(ComponentClassStr);
        if (ComponentClass == nullptr)
        {
            return JsonError(TEXT("add_component"), TEXT("unknown_component_class"), ComponentClassStr);
        }

        USimpleConstructionScript* SCS = Blueprint->SimpleConstructionScript;
        if (SCS == nullptr)
        {
            return JsonError(TEXT("add_component"), TEXT("no_scs"), BlueprintPath);
        }

        const FName ComponentFName(*ComponentName);
        if (SCS->FindSCSNode(ComponentFName) != nullptr)
        {
            return JsonError(TEXT("add_component"), TEXT("component_name_exists"), ComponentName);
        }

        USCS_Node* NewNode = SCS->CreateNode(ComponentClass, ComponentFName);
        if (NewNode == nullptr)
        {
            return JsonError(TEXT("add_component"), TEXT("scs_create_failed"), ComponentName);
        }
        SCS->AddNode(NewNode);

        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"add_component\",\"component_name\":%s,\"component_class\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(ComponentName),
            *EscapeJsonString(ComponentClass->GetName()),
            bSaved ? TEXT("true") : TEXT("false"));
    }

    // ===== Spike B8 — add_custom_event =====

    FString AddCustomEventOnGameThread(
        const FString& BlueprintPath,
        const FString& EventName,
        const FString& AnchorName,
        int32 PosX, int32 PosY)
    {
        check(IsInGameThread());

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr)
        {
            return JsonError(TEXT("add_custom_event"), TEXT("blueprint_not_found"), BlueprintPath);
        }
        if (Blueprint->UbergraphPages.Num() == 0)
        {
            return JsonError(TEXT("add_custom_event"), TEXT("no_event_graph"), BlueprintPath);
        }
        UEdGraph* EventGraph = Blueprint->UbergraphPages[0];

        // anchor uniqueness
        if (FindNodeByAnchor(EventGraph, AnchorName) != nullptr)
        {
            return JsonError(TEXT("add_custom_event"), TEXT("anchor_name_exists"), AnchorName);
        }

        // event-name uniqueness within EventGraph
        const FName EventFName(*EventName);
        for (const UEdGraphNode* Node : EventGraph->Nodes)
        {
            if (const UK2Node_CustomEvent* CE = Cast<UK2Node_CustomEvent>(Node))
            {
                if (CE->CustomFunctionName == EventFName)
                {
                    return JsonError(TEXT("add_custom_event"), TEXT("event_name_exists"), EventName);
                }
            }
        }

        UK2Node_CustomEvent* NewNode = NewObject<UK2Node_CustomEvent>(EventGraph);
        NewNode->SetFlags(RF_Transactional);
        NewNode->CustomFunctionName = EventFName;
        NewNode->NodePosX = PosX;
        NewNode->NodePosY = PosY;
        NewNode->NodeComment = AnchorName;
        NewNode->bCommentBubbleVisible = true;

        EventGraph->AddNode(NewNode, /*bFromUI*/ false, /*bSelectNewNode*/ false);
        NewNode->CreateNewGuid();
        NewNode->PostPlacedNewNode();
        NewNode->AllocateDefaultPins();

        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);

        const FString PinsJson = BuildPinsJsonArray(NewNode);
        const FString GuidStr = NewNode->NodeGuid.ToString(EGuidFormats::DigitsWithHyphens);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"add_custom_event\",\"anchor_name\":%s,\"event_name\":%s,\"node_guid\":%s,\"pins\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(AnchorName),
            *EscapeJsonString(EventName),
            *EscapeJsonString(GuidStr),
            *PinsJson,
            bSaved ? TEXT("true") : TEXT("false"));
    }

    // ===== Spike B9 — add_variable =====

    FString AddVariableOnGameThread(
        const FString& BlueprintPath,
        const FString& VarName,
        const FString& VarTypeKey,
        const FString& DefaultValue)
    {
        check(IsInGameThread());

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr)
        {
            return JsonError(TEXT("add_variable"), TEXT("blueprint_not_found"), BlueprintPath);
        }

        FEdGraphPinType PinType;
        if (!ResolveVariablePinType(VarTypeKey, PinType))
        {
            return JsonError(TEXT("add_variable"), TEXT("unknown_variable_type"), VarTypeKey);
        }

        const FName VarFName(*VarName);
        if (FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, VarFName) != INDEX_NONE)
        {
            return JsonError(TEXT("add_variable"), TEXT("variable_exists"), VarName);
        }

        const bool bAdded = FBlueprintEditorUtils::AddMemberVariable(Blueprint, VarFName, PinType, DefaultValue);
        if (!bAdded)
        {
            return JsonError(TEXT("add_variable"), TEXT("add_failed"), VarName);
        }

        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"add_variable\",\"variable_name\":%s,\"variable_type\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(VarName),
            *EscapeJsonString(VarTypeKey),
            bSaved ? TEXT("true") : TEXT("false"));
    }

    // ===== Spike B10 — add_variable_get / add_variable_set =====

    FString AddVariableRefOnGameThread(
        const FString& BlueprintPath,
        const FString& VariableName,
        const FString& AnchorName,
        int32 PosX, int32 PosY,
        bool bIsSet)
    {
        check(IsInGameThread());
        const TCHAR* CmdName = bIsSet ? TEXT("add_variable_set") : TEXT("add_variable_get");

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr)
        {
            return JsonError(CmdName, TEXT("blueprint_not_found"), BlueprintPath);
        }
        if (Blueprint->UbergraphPages.Num() == 0)
        {
            return JsonError(CmdName, TEXT("no_event_graph"), BlueprintPath);
        }
        UEdGraph* EventGraph = Blueprint->UbergraphPages[0];

        // Validate variable exists in BP
        const FName VarFName(*VariableName);
        if (FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, VarFName) == INDEX_NONE)
        {
            return JsonError(CmdName, TEXT("variable_not_found"),
                FString::Printf(TEXT("%s (call add_variable first)"), *VariableName));
        }

        // Anchor uniqueness
        if (FindNodeByAnchor(EventGraph, AnchorName) != nullptr)
        {
            return JsonError(CmdName, TEXT("anchor_name_exists"), AnchorName);
        }

        UK2Node_Variable* NewNode = nullptr;
        if (bIsSet)
        {
            UK2Node_VariableSet* SetNode = NewObject<UK2Node_VariableSet>(EventGraph);
            SetNode->VariableReference.SetSelfMember(VarFName);
            NewNode = SetNode;
        }
        else
        {
            UK2Node_VariableGet* GetNode = NewObject<UK2Node_VariableGet>(EventGraph);
            GetNode->VariableReference.SetSelfMember(VarFName);
            NewNode = GetNode;
        }

        NewNode->SetFlags(RF_Transactional);
        NewNode->NodePosX = PosX;
        NewNode->NodePosY = PosY;
        NewNode->NodeComment = AnchorName;
        NewNode->bCommentBubbleVisible = true;

        EventGraph->AddNode(NewNode, /*bFromUI*/ false, /*bSelectNewNode*/ false);
        NewNode->CreateNewGuid();
        NewNode->PostPlacedNewNode();
        NewNode->AllocateDefaultPins();

        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);

        const FString PinsJson = BuildPinsJsonArray(NewNode);
        const FString GuidStr = NewNode->NodeGuid.ToString(EGuidFormats::DigitsWithHyphens);
        const FString CmdStr = bIsSet ? TEXT("add_variable_set") : TEXT("add_variable_get");

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":%s,\"anchor_name\":%s,\"variable_name\":%s,\"node_guid\":%s,\"pins\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(CmdStr),
            *EscapeJsonString(AnchorName),
            *EscapeJsonString(VariableName),
            *EscapeJsonString(GuidStr),
            *PinsJson,
            bSaved ? TEXT("true") : TEXT("false"));
    }

    // ===== v4 helpers =====

    /** Whitelist of macros from /Engine/EditorBlueprintResources/StandardMacros. */
    bool IsKnownMacro(const FString& MacroName)
    {
        static const TSet<FString> kKnown = {
            TEXT("ForEachLoop"), TEXT("ForLoop"), TEXT("WhileLoop"),
            TEXT("FlipFlop"), TEXT("DoOnce"), TEXT("Gate"), TEXT("IsValid"),
        };
        return kKnown.Contains(MacroName);
    }

    /** Find a macro graph by name in StandardMacros library. */
    UEdGraph* FindStandardMacro(const FString& MacroName)
    {
        const TCHAR* MacroLibPath = TEXT("/Engine/EditorBlueprintResources/StandardMacros.StandardMacros");
        UBlueprint* MacroLib = LoadObject<UBlueprint>(nullptr, MacroLibPath);
        if (MacroLib == nullptr) return nullptr;
        for (UEdGraph* G : MacroLib->MacroGraphs)
        {
            if (G != nullptr && G->GetName().Equals(MacroName, ESearchCase::IgnoreCase))
            {
                return G;
            }
        }
        return nullptr;
    }

    /** Format struct default value to UE-canonical form. v4 supports Vector / Rotator / LinearColor / Color. */
    FString FormatStructDefault(UScriptStruct* StructType, const FString& UserInput)
    {
        const FString Trimmed = UserInput.TrimStartAndEnd();

        // If already in UE format (starts with "(X=" / "(R=" etc.), pass through
        if (Trimmed.StartsWith(TEXT("(")) && Trimmed.Contains(TEXT("=")))
        {
            return Trimmed;
        }

        TArray<FString> Parts;
        Trimmed.ParseIntoArray(Parts, TEXT(","), /*InCullEmpty*/ true);
        for (FString& P : Parts) { P = P.TrimStartAndEnd(); }

        if (StructType == TBaseStructure<FVector>::Get())
        {
            if (Parts.Num() != 3) return Trimmed;
            return FString::Printf(TEXT("(X=%s,Y=%s,Z=%s)"), *Parts[0], *Parts[1], *Parts[2]);
        }
        if (StructType == TBaseStructure<FRotator>::Get())
        {
            if (Parts.Num() != 3) return Trimmed;
            // UE Rotator format: (P=pitch,Y=yaw,R=roll). User input convention: "P,Y,R".
            return FString::Printf(TEXT("(P=%s,Y=%s,R=%s)"), *Parts[0], *Parts[1], *Parts[2]);
        }
        if (StructType == TBaseStructure<FLinearColor>::Get() || StructType == TBaseStructure<FColor>::Get())
        {
            // Accept "R,G,B" (alpha=1) or "R,G,B,A"
            if (Parts.Num() == 3)
            {
                return FString::Printf(TEXT("(R=%s,G=%s,B=%s,A=1.0)"), *Parts[0], *Parts[1], *Parts[2]);
            }
            if (Parts.Num() == 4)
            {
                return FString::Printf(TEXT("(R=%s,G=%s,B=%s,A=%s)"),
                    *Parts[0], *Parts[1], *Parts[2], *Parts[3]);
            }
            return Trimmed;
        }
        return Trimmed;
    }

    /** True if we know how to format a default for this struct type. */
    bool IsSupportedStructForDefault(UScriptStruct* StructType)
    {
        if (StructType == nullptr) return false;
        return StructType == TBaseStructure<FVector>::Get()
            || StructType == TBaseStructure<FRotator>::Get()
            || StructType == TBaseStructure<FLinearColor>::Get()
            || StructType == TBaseStructure<FColor>::Get();
    }

    // ===== v3 — add_cast helpers =====

    /** Map a user-friendly cast target name to a UClass*. v3 whitelist + qualified fallback. */
    UClass* ResolveCastTargetClass(const FString& Name)
    {
        if (Name.Equals(TEXT("Pawn"), ESearchCase::IgnoreCase))                  return APawn::StaticClass();
        if (Name.Equals(TEXT("Character"), ESearchCase::IgnoreCase))             return ACharacter::StaticClass();
        if (Name.Equals(TEXT("Actor"), ESearchCase::IgnoreCase))                 return AActor::StaticClass();
        if (Name.Equals(TEXT("PlayerController"), ESearchCase::IgnoreCase))      return APlayerController::StaticClass();
        if (Name.Equals(TEXT("PlayerCameraManager"), ESearchCase::IgnoreCase))   return APlayerCameraManager::StaticClass();
        if (Name.Equals(TEXT("GameMode"), ESearchCase::IgnoreCase) ||
            Name.Equals(TEXT("GameModeBase"), ESearchCase::IgnoreCase))          return AGameModeBase::StaticClass();
        if (Name.Equals(TEXT("PlayerState"), ESearchCase::IgnoreCase))           return APlayerState::StaticClass();
        if (Name.Equals(TEXT("HUD"), ESearchCase::IgnoreCase))                   return AHUD::StaticClass();

        // Qualified fallback: any UObject-derived class (engine native or BP-generated)
        if (UClass* Found = FindFirstObject<UClass>(*Name, EFindFirstObjectOptions::NativeFirst))
        {
            return Found;
        }
        // Also try with "BP_" prefix or with "_C" suffix for BP classes? v3 keeps it simple.
        return nullptr;
    }

    // ===== v5 — add_function (create user function graph) =====

    FString AddFunctionOnGameThread(const FString& BlueprintPath, const FString& FunctionName)
    {
        check(IsInGameThread());

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (!Blueprint) return JsonError(TEXT("add_function"), TEXT("blueprint_not_found"), BlueprintPath);

        const FName FuncFName(*FunctionName);
        // Check for name collision with existing function graphs
        for (const UEdGraph* G : Blueprint->FunctionGraphs)
        {
            if (G && G->GetFName() == FuncFName)
            {
                return JsonError(TEXT("add_function"), TEXT("function_exists"), FunctionName);
            }
        }

        UEdGraph* NewFuncGraph = FBlueprintEditorUtils::CreateNewGraph(
            Blueprint, FuncFName, UEdGraph::StaticClass(), UEdGraphSchema_K2::StaticClass());
        if (!NewFuncGraph)
        {
            return JsonError(TEXT("add_function"), TEXT("graph_create_failed"), FunctionName);
        }

        FBlueprintEditorUtils::AddFunctionGraph<UClass>(
            Blueprint, NewFuncGraph, /*bIsUserCreated*/ true, /*SignatureClass*/ nullptr);

        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, false);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"add_function\",\"function_name\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(FunctionName), bSaved ? TEXT("true") : TEXT("false"));
    }

    // ===== v5 — call_blueprint_function (cross-BP function call) =====

    /** Resolve a target class for cross-BP calls. Tries native, then BP class paths. */
    UClass* ResolveCallTargetClass(const FString& Name)
    {
        // Try as native class name first
        if (UClass* Native = FindUClassByName(Name)) return Native;

        // Try as BP path. Normalize:
        //   "BP_X"                    → "/Game/Blueprints/BP_X.BP_X_C"
        //   "/Game/X/BP_Y"            → "/Game/X/BP_Y.BP_Y_C"
        //   "/Game/X/BP_Y.BP_Y_C"     → as-is
        FString BPPath = Name;
        if (!BPPath.StartsWith(TEXT("/Game/")))
        {
            BPPath = FString::Printf(TEXT("/Game/Blueprints/%s"), *Name);
        }
        // First try to load as Blueprint asset and get GeneratedClass
        UBlueprint* TargetBP = LoadObject<UBlueprint>(nullptr, *BPPath);
        if (TargetBP && TargetBP->GeneratedClass) return TargetBP->GeneratedClass;

        // Last resort: try loading as a class directly (with _C suffix)
        FString ClassPath = BPPath + TEXT(".") + FPaths::GetBaseFilename(BPPath) + TEXT("_C");
        if (UClass* DirectClass = LoadObject<UClass>(nullptr, *ClassPath)) return DirectClass;

        return nullptr;
    }

    FString CallBlueprintFunctionOnGameThread(
        const FString& BlueprintPath,
        const FString& TargetClassStr,
        const FString& FunctionName,
        const FString& AnchorName,
        int32 PosX, int32 PosY)
    {
        check(IsInGameThread());

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (!Blueprint) return JsonError(TEXT("call_blueprint_function"), TEXT("blueprint_not_found"), BlueprintPath);
        if (Blueprint->UbergraphPages.Num() == 0)
            return JsonError(TEXT("call_blueprint_function"), TEXT("no_event_graph"), BlueprintPath);
        UEdGraph* EventGraph = Blueprint->UbergraphPages[0];

        if (FindNodeByAnchor(EventGraph, AnchorName))
            return JsonError(TEXT("call_blueprint_function"), TEXT("anchor_name_exists"), AnchorName);

        UClass* TargetClass = ResolveCallTargetClass(TargetClassStr);
        if (!TargetClass)
            return JsonError(TEXT("call_blueprint_function"), TEXT("target_class_not_found"), TargetClassStr);

        UFunction* TargetFunc = TargetClass->FindFunctionByName(FName(*FunctionName));
        if (!TargetFunc)
            return JsonError(TEXT("call_blueprint_function"), TEXT("function_not_found"),
                FString::Printf(TEXT("%s on %s"), *FunctionName, *TargetClass->GetName()));

        UK2Node_CallFunction* NewNode = NewObject<UK2Node_CallFunction>(EventGraph);
        NewNode->SetFlags(RF_Transactional);
        NewNode->FunctionReference.SetExternalMember(TargetFunc->GetFName(), TargetClass);
        NewNode->NodePosX = PosX;
        NewNode->NodePosY = PosY;
        NewNode->NodeComment = AnchorName;
        NewNode->bCommentBubbleVisible = true;

        EventGraph->AddNode(NewNode, false, false);
        NewNode->CreateNewGuid();
        NewNode->PostPlacedNewNode();
        NewNode->AllocateDefaultPins();

        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, false);

        const FString PinsJson = BuildPinsJsonArray(NewNode);
        const FString GuidStr = NewNode->NodeGuid.ToString(EGuidFormats::DigitsWithHyphens);
        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"call_blueprint_function\",\"anchor_name\":%s,\"node_guid\":%s,\"target_class\":%s,\"function\":%s,\"pins\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(AnchorName), *EscapeJsonString(GuidStr),
            *EscapeJsonString(TargetClass->GetName()), *EscapeJsonString(FunctionName),
            *PinsJson, bSaved ? TEXT("true") : TEXT("false"));
    }

    // ===== v5 — Enhanced Input =====

    bool ResolveInputActionValueType(const FString& Key, EInputActionValueType& OutType)
    {
        if (Key.Equals(TEXT("Boolean"), ESearchCase::IgnoreCase) ||
            Key.Equals(TEXT("bool"), ESearchCase::IgnoreCase))
        {
            OutType = EInputActionValueType::Boolean; return true;
        }
        if (Key.Equals(TEXT("Axis1D"), ESearchCase::IgnoreCase) ||
            Key.Equals(TEXT("float"), ESearchCase::IgnoreCase))
        {
            OutType = EInputActionValueType::Axis1D; return true;
        }
        if (Key.Equals(TEXT("Axis2D"), ESearchCase::IgnoreCase) ||
            Key.Equals(TEXT("Vector2D"), ESearchCase::IgnoreCase))
        {
            OutType = EInputActionValueType::Axis2D; return true;
        }
        if (Key.Equals(TEXT("Axis3D"), ESearchCase::IgnoreCase) ||
            Key.Equals(TEXT("Vector"), ESearchCase::IgnoreCase))
        {
            OutType = EInputActionValueType::Axis3D; return true;
        }
        return false;
    }

    FString CreateInputActionOnGameThread(const FString& Name, const FString& ValueTypeStr, const FString& Path)
    {
        check(IsInGameThread());

        EInputActionValueType ValueType;
        if (!ResolveInputActionValueType(ValueTypeStr, ValueType))
        {
            return JsonError(TEXT("create_input_action"), TEXT("unknown_value_type"),
                FString::Printf(TEXT("%s (use: Boolean, Axis1D, Axis2D, Axis3D)"), *ValueTypeStr));
        }

        const FString FullPath = Path / Name;
        if (UEditorAssetLibrary::DoesAssetExist(FullPath))
        {
            return JsonError(TEXT("create_input_action"), TEXT("asset_exists"), FullPath);
        }

        FAssetToolsModule& AssetToolsModule = FModuleManager::LoadModuleChecked<FAssetToolsModule>("AssetTools");
        UObject* NewAsset = AssetToolsModule.Get().CreateAsset(Name, Path, UInputAction::StaticClass(), nullptr);
        if (!NewAsset)
        {
            return JsonError(TEXT("create_input_action"), TEXT("creation_failed"), FullPath);
        }
        UInputAction* IA = Cast<UInputAction>(NewAsset);
        if (!IA)
        {
            return JsonError(TEXT("create_input_action"), TEXT("not_input_action"), FullPath);
        }
        IA->ValueType = ValueType;
        IA->MarkPackageDirty();
        const bool bSaved = UEditorAssetLibrary::SaveAsset(FullPath, false);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"create_input_action\",\"action_path\":%s,\"value_type\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(FullPath), *EscapeJsonString(ValueTypeStr),
            bSaved ? TEXT("true") : TEXT("false"));
    }

    FString CreateInputMappingContextOnGameThread(const FString& Name, const FString& Path)
    {
        check(IsInGameThread());

        const FString FullPath = Path / Name;
        if (UEditorAssetLibrary::DoesAssetExist(FullPath))
        {
            return JsonError(TEXT("create_input_mapping_context"), TEXT("asset_exists"), FullPath);
        }

        FAssetToolsModule& AssetToolsModule = FModuleManager::LoadModuleChecked<FAssetToolsModule>("AssetTools");
        UObject* NewAsset = AssetToolsModule.Get().CreateAsset(Name, Path, UInputMappingContext::StaticClass(), nullptr);
        if (!NewAsset)
        {
            return JsonError(TEXT("create_input_mapping_context"), TEXT("creation_failed"), FullPath);
        }
        const bool bSaved = UEditorAssetLibrary::SaveAsset(FullPath, false);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"create_input_mapping_context\",\"imc_path\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(FullPath), bSaved ? TEXT("true") : TEXT("false"));
    }

    FString AddMappingToImcOnGameThread(const FString& IMCPath, const FString& ActionPath, const FString& KeyName)
    {
        check(IsInGameThread());

        UInputMappingContext* IMC = LoadObject<UInputMappingContext>(nullptr, *IMCPath);
        if (!IMC) return JsonError(TEXT("add_mapping_to_imc"), TEXT("imc_not_found"), IMCPath);

        UInputAction* IA = LoadObject<UInputAction>(nullptr, *ActionPath);
        if (!IA) return JsonError(TEXT("add_mapping_to_imc"), TEXT("action_not_found"), ActionPath);

        const FKey Key = ResolveFKeyWithAliases(KeyName);
        if (!Key.IsValid())
            return JsonError(TEXT("add_mapping_to_imc"), TEXT("invalid_key"),
                FString::Printf(TEXT("%s (try: P, SpaceBar/Space, LeftMouseButton, F1, etc.)"), *KeyName));

        IMC->MapKey(IA, Key);
        IMC->MarkPackageDirty();
        const bool bSaved = UEditorAssetLibrary::SaveAsset(IMCPath, false);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"add_mapping_to_imc\",\"imc_path\":%s,\"action_path\":%s,\"key\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(IMCPath), *EscapeJsonString(ActionPath),
            *EscapeJsonString(Key.ToString()), bSaved ? TEXT("true") : TEXT("false"));
    }

    FString AddEnhancedInputNodeOnGameThread(
        const FString& BlueprintPath,
        const FString& ActionPath,
        const FString& AnchorName,
        int32 PosX, int32 PosY)
    {
        check(IsInGameThread());

        UInputAction* IA = LoadObject<UInputAction>(nullptr, *ActionPath);
        if (!IA) return JsonError(TEXT("add_enhanced_input_node"), TEXT("action_not_found"), ActionPath);

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (!Blueprint) return JsonError(TEXT("add_enhanced_input_node"), TEXT("blueprint_not_found"), BlueprintPath);
        if (Blueprint->UbergraphPages.Num() == 0)
            return JsonError(TEXT("add_enhanced_input_node"), TEXT("no_event_graph"), BlueprintPath);
        UEdGraph* EventGraph = Blueprint->UbergraphPages[0];

        if (FindNodeByAnchor(EventGraph, AnchorName))
            return JsonError(TEXT("add_enhanced_input_node"), TEXT("anchor_name_exists"), AnchorName);

        UK2Node_EnhancedInputAction* NewNode = NewObject<UK2Node_EnhancedInputAction>(EventGraph);
        NewNode->SetFlags(RF_Transactional);
        NewNode->InputAction = IA;
        NewNode->NodePosX = PosX;
        NewNode->NodePosY = PosY;
        NewNode->NodeComment = AnchorName;
        NewNode->bCommentBubbleVisible = true;

        EventGraph->AddNode(NewNode, false, false);
        NewNode->CreateNewGuid();
        NewNode->PostPlacedNewNode();
        NewNode->AllocateDefaultPins();

        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, false);

        const FString PinsJson = BuildPinsJsonArray(NewNode);
        const FString GuidStr = NewNode->NodeGuid.ToString(EGuidFormats::DigitsWithHyphens);
        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"add_enhanced_input_node\",\"anchor_name\":%s,\"node_guid\":%s,\"action_path\":%s,\"pins\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(AnchorName), *EscapeJsonString(GuidStr),
            *EscapeJsonString(ActionPath), *PinsJson,
            bSaved ? TEXT("true") : TEXT("false"));
    }

    // ===== v4 — add_macro (K2Node_MacroInstance) =====

    FString AddMacroOnGameThread(
        const FString& BlueprintPath,
        const FString& MacroType,
        const FString& AnchorName,
        int32 PosX, int32 PosY)
    {
        check(IsInGameThread());

        if (!IsKnownMacro(MacroType))
        {
            return JsonError(TEXT("add_macro"), TEXT("unknown_macro_type"),
                FString::Printf(TEXT("%s (known: ForEachLoop, ForLoop, WhileLoop, FlipFlop, DoOnce, Gate, IsValid)"), *MacroType));
        }

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (!Blueprint) return JsonError(TEXT("add_macro"), TEXT("blueprint_not_found"), BlueprintPath);
        if (Blueprint->UbergraphPages.Num() == 0) return JsonError(TEXT("add_macro"), TEXT("no_event_graph"), BlueprintPath);
        UEdGraph* EventGraph = Blueprint->UbergraphPages[0];

        if (FindNodeByAnchor(EventGraph, AnchorName) != nullptr)
            return JsonError(TEXT("add_macro"), TEXT("anchor_name_exists"), AnchorName);

        UEdGraph* MacroGraph = FindStandardMacro(MacroType);
        if (MacroGraph == nullptr)
        {
            return JsonError(TEXT("add_macro"), TEXT("macro_graph_not_found"),
                FString::Printf(TEXT("%s in /Engine/EditorBlueprintResources/StandardMacros"), *MacroType));
        }

        UK2Node_MacroInstance* NewNode = NewObject<UK2Node_MacroInstance>(EventGraph);
        NewNode->SetFlags(RF_Transactional);
        NewNode->SetMacroGraph(MacroGraph);
        NewNode->NodePosX = PosX;
        NewNode->NodePosY = PosY;
        NewNode->NodeComment = AnchorName;
        NewNode->bCommentBubbleVisible = true;

        EventGraph->AddNode(NewNode, false, false);
        NewNode->CreateNewGuid();
        NewNode->PostPlacedNewNode();
        NewNode->AllocateDefaultPins();

        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, false);

        const FString PinsJson = BuildPinsJsonArray(NewNode);
        const FString GuidStr = NewNode->NodeGuid.ToString(EGuidFormats::DigitsWithHyphens);
        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"add_macro\",\"anchor_name\":%s,\"node_guid\":%s,\"macro_type\":%s,\"pins\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(AnchorName), *EscapeJsonString(GuidStr), *EscapeJsonString(MacroType), *PinsJson,
            bSaved ? TEXT("true") : TEXT("false"));
    }

    // ===== v4 — add_self_reference (K2Node_Self) =====

    FString AddSelfReferenceOnGameThread(
        const FString& BlueprintPath,
        const FString& AnchorName,
        int32 PosX, int32 PosY)
    {
        check(IsInGameThread());

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (!Blueprint) return JsonError(TEXT("add_self_reference"), TEXT("blueprint_not_found"), BlueprintPath);
        if (Blueprint->UbergraphPages.Num() == 0) return JsonError(TEXT("add_self_reference"), TEXT("no_event_graph"), BlueprintPath);
        UEdGraph* EventGraph = Blueprint->UbergraphPages[0];

        if (FindNodeByAnchor(EventGraph, AnchorName) != nullptr)
            return JsonError(TEXT("add_self_reference"), TEXT("anchor_name_exists"), AnchorName);

        UK2Node_Self* NewNode = NewObject<UK2Node_Self>(EventGraph);
        NewNode->SetFlags(RF_Transactional);
        NewNode->NodePosX = PosX;
        NewNode->NodePosY = PosY;
        NewNode->NodeComment = AnchorName;
        NewNode->bCommentBubbleVisible = true;

        EventGraph->AddNode(NewNode, false, false);
        NewNode->CreateNewGuid();
        NewNode->PostPlacedNewNode();
        NewNode->AllocateDefaultPins();

        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, false);

        const FString PinsJson = BuildPinsJsonArray(NewNode);
        const FString GuidStr = NewNode->NodeGuid.ToString(EGuidFormats::DigitsWithHyphens);
        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"add_self_reference\",\"anchor_name\":%s,\"node_guid\":%s,\"pins\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(AnchorName), *EscapeJsonString(GuidStr), *PinsJson,
            bSaved ? TEXT("true") : TEXT("false"));
    }

    // ===== v4 — add_input_key (K2Node_InputKey) =====

    FString AddInputKeyOnGameThread(
        const FString& BlueprintPath,
        const FString& KeyName,
        const FString& AnchorName,
        int32 PosX, int32 PosY)
    {
        check(IsInGameThread());

        const FKey Key = ResolveFKeyWithAliases(KeyName);
        if (!Key.IsValid())
        {
            return JsonError(TEXT("add_input_key"), TEXT("invalid_key"),
                FString::Printf(TEXT("%s (try: P, SpaceBar/Space, LeftMouseButton, F1, etc.)"), *KeyName));
        }

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (!Blueprint) return JsonError(TEXT("add_input_key"), TEXT("blueprint_not_found"), BlueprintPath);
        if (Blueprint->UbergraphPages.Num() == 0) return JsonError(TEXT("add_input_key"), TEXT("no_event_graph"), BlueprintPath);
        UEdGraph* EventGraph = Blueprint->UbergraphPages[0];

        if (FindNodeByAnchor(EventGraph, AnchorName) != nullptr)
            return JsonError(TEXT("add_input_key"), TEXT("anchor_name_exists"), AnchorName);

        UK2Node_InputKey* NewNode = NewObject<UK2Node_InputKey>(EventGraph);
        NewNode->SetFlags(RF_Transactional);
        NewNode->InputKey = Key;
        NewNode->NodePosX = PosX;
        NewNode->NodePosY = PosY;
        NewNode->NodeComment = AnchorName;
        NewNode->bCommentBubbleVisible = true;

        EventGraph->AddNode(NewNode, false, false);
        NewNode->CreateNewGuid();
        NewNode->PostPlacedNewNode();
        NewNode->AllocateDefaultPins();

        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, false);

        const FString PinsJson = BuildPinsJsonArray(NewNode);
        const FString GuidStr = NewNode->NodeGuid.ToString(EGuidFormats::DigitsWithHyphens);
        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"add_input_key\",\"anchor_name\":%s,\"node_guid\":%s,\"key\":%s,\"pins\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(AnchorName), *EscapeJsonString(GuidStr),
            *EscapeJsonString(Key.ToString()), *PinsJson,
            bSaved ? TEXT("true") : TEXT("false"));
    }

    // ===== v4 — delete_node =====

    FString DeleteNodeOnGameThread(const FString& BlueprintPath, const FString& AnchorName)
    {
        check(IsInGameThread());

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (!Blueprint) return JsonError(TEXT("delete_node"), TEXT("blueprint_not_found"), BlueprintPath);
        if (Blueprint->UbergraphPages.Num() == 0) return JsonError(TEXT("delete_node"), TEXT("no_event_graph"), BlueprintPath);
        UEdGraph* EventGraph = Blueprint->UbergraphPages[0];

        // Strict lookup (NOT FindOrSpawn — don't spawn just to delete)
        UEdGraphNode* TargetNode = FindNodeByAnchor(EventGraph, AnchorName);
        if (TargetNode == nullptr)
        {
            return JsonError(TEXT("delete_node"), TEXT("anchor_not_found"), AnchorName);
        }

        const FString NodeClass = TargetNode->GetClass()->GetName();
        TargetNode->Modify();
        TargetNode->DestroyNode();   // breaks all pin links + removes from graph

        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, false);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"delete_node\",\"anchor_name\":%s,\"node_type\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(AnchorName), *EscapeJsonString(NodeClass),
            bSaved ? TEXT("true") : TEXT("false"));
    }

    // ===== v4 — disconnect_pins =====

    FString DisconnectPinsOnGameThread(
        const FString& BlueprintPath,
        const FString& FromPinRef,
        const FString& ToPinRef)
    {
        check(IsInGameThread());

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (!Blueprint) return JsonError(TEXT("disconnect_pins"), TEXT("blueprint_not_found"), BlueprintPath);
        if (Blueprint->UbergraphPages.Num() == 0) return JsonError(TEXT("disconnect_pins"), TEXT("no_event_graph"), BlueprintPath);
        UEdGraph* EventGraph = Blueprint->UbergraphPages[0];

        FString FromErr, ToErr;
        UEdGraphPin* FromPin = ResolvePinRef(EventGraph, FromPinRef, TEXT("disconnect_pins"), FromErr);
        if (!FromPin) return FromErr;
        UEdGraphPin* ToPin = ResolvePinRef(EventGraph, ToPinRef, TEXT("disconnect_pins"), ToErr);
        if (!ToPin) return ToErr;

        // Verify they're actually connected
        if (!FromPin->LinkedTo.Contains(ToPin))
        {
            return JsonError(TEXT("disconnect_pins"), TEXT("not_connected"),
                FString::Printf(TEXT("%s -> %s"), *FromPinRef, *ToPinRef));
        }

        const UEdGraphSchema* Schema = EventGraph->GetSchema();
        Schema->BreakSinglePinLink(FromPin, ToPin);

        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, false);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"disconnect_pins\",\"from\":%s,\"to\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(FromPinRef), *EscapeJsonString(ToPinRef),
            bSaved ? TEXT("true") : TEXT("false"));
    }

    // ===== v3 — add_branch (K2Node_IfThenElse) =====

    FString AddBranchOnGameThread(
        const FString& BlueprintPath,
        const FString& AnchorName,
        int32 PosX, int32 PosY)
    {
        check(IsInGameThread());

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr)
        {
            return JsonError(TEXT("add_branch"), TEXT("blueprint_not_found"), BlueprintPath);
        }
        if (Blueprint->UbergraphPages.Num() == 0)
        {
            return JsonError(TEXT("add_branch"), TEXT("no_event_graph"), BlueprintPath);
        }
        UEdGraph* EventGraph = Blueprint->UbergraphPages[0];

        if (FindNodeByAnchor(EventGraph, AnchorName) != nullptr)
        {
            return JsonError(TEXT("add_branch"), TEXT("anchor_name_exists"), AnchorName);
        }

        UK2Node_IfThenElse* NewNode = NewObject<UK2Node_IfThenElse>(EventGraph);
        NewNode->SetFlags(RF_Transactional);
        NewNode->NodePosX = PosX;
        NewNode->NodePosY = PosY;
        NewNode->NodeComment = AnchorName;
        NewNode->bCommentBubbleVisible = true;

        EventGraph->AddNode(NewNode, /*bFromUI*/ false, /*bSelectNewNode*/ false);
        NewNode->CreateNewGuid();
        NewNode->PostPlacedNewNode();
        NewNode->AllocateDefaultPins();

        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);

        const FString PinsJson = BuildPinsJsonArray(NewNode);
        const FString GuidStr = NewNode->NodeGuid.ToString(EGuidFormats::DigitsWithHyphens);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"add_branch\",\"anchor_name\":%s,\"node_guid\":%s,\"node_type\":\"K2Node_IfThenElse\",\"pins\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(AnchorName),
            *EscapeJsonString(GuidStr),
            *PinsJson,
            bSaved ? TEXT("true") : TEXT("false"));
    }

    // ===== v3 — add_cast (K2Node_DynamicCast) =====

    FString AddCastOnGameThread(
        const FString& BlueprintPath,
        const FString& TargetClassStr,
        const FString& AnchorName,
        int32 PosX, int32 PosY)
    {
        check(IsInGameThread());

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr)
        {
            return JsonError(TEXT("add_cast"), TEXT("blueprint_not_found"), BlueprintPath);
        }
        if (Blueprint->UbergraphPages.Num() == 0)
        {
            return JsonError(TEXT("add_cast"), TEXT("no_event_graph"), BlueprintPath);
        }
        UEdGraph* EventGraph = Blueprint->UbergraphPages[0];

        if (FindNodeByAnchor(EventGraph, AnchorName) != nullptr)
        {
            return JsonError(TEXT("add_cast"), TEXT("anchor_name_exists"), AnchorName);
        }

        UClass* TargetClass = ResolveCastTargetClass(TargetClassStr);
        if (TargetClass == nullptr)
        {
            return JsonError(TEXT("add_cast"), TEXT("unknown_target_class"), TargetClassStr);
        }

        UK2Node_DynamicCast* NewNode = NewObject<UK2Node_DynamicCast>(EventGraph);
        NewNode->SetFlags(RF_Transactional);
        NewNode->TargetType = TargetClass;   // MUST set before AllocateDefaultPins so pins reflect target type
        NewNode->NodePosX = PosX;
        NewNode->NodePosY = PosY;
        NewNode->NodeComment = AnchorName;
        NewNode->bCommentBubbleVisible = true;

        EventGraph->AddNode(NewNode, /*bFromUI*/ false, /*bSelectNewNode*/ false);
        NewNode->CreateNewGuid();
        NewNode->PostPlacedNewNode();
        NewNode->AllocateDefaultPins();

        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);

        const FString PinsJson = BuildPinsJsonArray(NewNode);
        const FString GuidStr = NewNode->NodeGuid.ToString(EGuidFormats::DigitsWithHyphens);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"add_cast\",\"anchor_name\":%s,\"node_guid\":%s,\"node_type\":\"K2Node_DynamicCast\",\"target_class\":%s,\"pins\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(AnchorName),
            *EscapeJsonString(GuidStr),
            *EscapeJsonString(TargetClass->GetName()),
            *PinsJson,
            bSaved ? TEXT("true") : TEXT("false"));
    }

    // ===== v2 — get_blueprint =====

    /**
     * Derive a stable, human-readable anchor for a node when describing it
     * back to the LLM.
     *
     * Priority (revised after v2.0.1 bugs):
     *   - K2Node_CustomEvent (checked FIRST since CustomEvent IS-A Event):
     *       NodeComment (anchor_name from add_custom_event) → CustomFunctionName → guid
     *   - K2Node_Event (non-custom):
     *       reverse-map well-known events FIRST (avoids picking up UE's
     *       garbage instructional NodeComment on disabled Tick placeholders)
     *       → fallback: stripped-receive lowercase
     *   - All other nodes:
     *       NodeComment (the anchor_name from add_node / add_variable_*) → guid
     */
    FString DeriveAnchorForNode(const UEdGraphNode* Node)
    {
        // Custom event: NodeComment (anchor_name) > CustomFunctionName > guid
        if (const UK2Node_CustomEvent* CE = Cast<UK2Node_CustomEvent>(Node))
        {
            if (!Node->NodeComment.IsEmpty())
            {
                return Node->NodeComment;
            }
            if (CE->CustomFunctionName != NAME_None)
            {
                return CE->CustomFunctionName.ToString();
            }
            // fall through to guid
        }
        // Regular Event: reverse-map FIRST (skip possibly-garbage NodeComment)
        else if (const UK2Node_Event* EventNode = Cast<UK2Node_Event>(Node))
        {
            const FName Fn = EventNode->EventReference.GetMemberName();
            // Reverse map well-known events
            if (Fn == TEXT("ReceiveBeginPlay"))         return TEXT("begin_play");
            if (Fn == TEXT("ReceiveTick"))              return TEXT("tick");
            if (Fn == TEXT("ReceiveEndPlay"))           return TEXT("end_play");
            if (Fn == TEXT("ReceiveActorBeginOverlap")) return TEXT("actor_begin_overlap");
            if (Fn == TEXT("ReceiveActorEndOverlap"))   return TEXT("actor_end_overlap");
            if (Fn == TEXT("ReceiveHit"))               return TEXT("hit");
            if (Fn == TEXT("ReceiveDestroyed"))         return TEXT("destroyed");
            // Generic event: strip "Receive" prefix, lowercase
            if (Fn != NAME_None)
            {
                FString FnStr = Fn.ToString();
                if (FnStr.StartsWith(TEXT("Receive")))
                {
                    FnStr = FnStr.RightChop(7);
                }
                return FnStr.ToLower();
            }
            // fall through (no event ref — unusual)
        }

        // Non-event nodes (add_node / add_variable_* / etc.) or events without identifying info
        if (!Node->NodeComment.IsEmpty())
        {
            return Node->NodeComment;
        }

        // Fallback: short guid label, stable across sessions
        const FString GuidStr = Node->NodeGuid.ToString(EGuidFormats::DigitsLower);
        return FString::Printf(TEXT("node_%s"), *GuidStr.Left(8));
    }

    /** Write a single node's anchor entry into the JSON writer (caller has set the object key). */
    template <typename WriterRef>
    void WriteNodeAnchor(WriterRef Writer, const UEdGraphNode* Node)
    {
        Writer->WriteObjectStart();
        Writer->WriteValue(TEXT("k2_node_class"), Node->GetClass()->GetName());
        Writer->WriteArrayStart(TEXT("position"));
        Writer->WriteValue(static_cast<double>(Node->NodePosX));
        Writer->WriteValue(static_cast<double>(Node->NodePosY));
        Writer->WriteArrayEnd();

        // Node-type-specific fields — IMPORTANT: cast CustomEvent FIRST because
        // UK2Node_CustomEvent IS-A UK2Node_Event. v2.0.1 bug fix.
        if (const UK2Node_CustomEvent* CE = Cast<UK2Node_CustomEvent>(Node))
        {
            Writer->WriteValue(TEXT("event_name"), CE->CustomFunctionName.ToString());
        }
        else if (const UK2Node_Event* EventNode = Cast<UK2Node_Event>(Node))
        {
            Writer->WriteValue(TEXT("event_name"), EventNode->EventReference.GetMemberName().ToString());
        }
        else if (const UK2Node_CallFunction* CallFn = Cast<UK2Node_CallFunction>(Node))
        {
            Writer->WriteValue(TEXT("function"), CallFn->FunctionReference.GetMemberName().ToString());
            const UClass* OwningClass = CallFn->FunctionReference.GetMemberParentClass();
            Writer->WriteValue(TEXT("owning_class"), OwningClass ? OwningClass->GetName() : TEXT(""));
        }
        else if (const UK2Node_VariableGet* VarGet = Cast<UK2Node_VariableGet>(Node))
        {
            Writer->WriteValue(TEXT("variable_name"), VarGet->VariableReference.GetMemberName().ToString());
        }
        else if (const UK2Node_VariableSet* VarSet = Cast<UK2Node_VariableSet>(Node))
        {
            Writer->WriteValue(TEXT("variable_name"), VarSet->VariableReference.GetMemberName().ToString());
        }

        // Pins
        Writer->WriteArrayStart(TEXT("pins"));
        for (const UEdGraphPin* Pin : Node->Pins)
        {
            Writer->WriteObjectStart();
            Writer->WriteValue(TEXT("name"), Pin->PinName.ToString());
            Writer->WriteValue(TEXT("direction"),
                Pin->Direction == EGPD_Input ? TEXT("input") : TEXT("output"));
            Writer->WriteValue(TEXT("type"), Pin->PinType.PinCategory.ToString());
            // Default value for primitive input pins
            if (Pin->Direction == EGPD_Input
                && Pin->PinType.PinCategory != UEdGraphSchema_K2::PC_Exec
                && !Pin->DefaultValue.IsEmpty())
            {
                Writer->WriteValue(TEXT("default"), Pin->DefaultValue);
            }
            // Linked flag — useful for LLM to know what's already wired
            if (Pin->LinkedTo.Num() > 0)
            {
                Writer->WriteValue(TEXT("linked"), true);
            }
            Writer->WriteObjectEnd();
        }
        Writer->WriteArrayEnd();

        Writer->WriteObjectEnd();
    }

    /**
     * Snapshot a Blueprint. MUST run on the game thread.
     * Returns a complete JSON response line (with trailing \n).
     */
    FString GetBlueprintOnGameThread(const FString& BlueprintPath)
    {
        check(IsInGameThread());

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr)
        {
            return JsonError(TEXT("get_blueprint"), TEXT("blueprint_not_found"), BlueprintPath);
        }

        // Build per-node anchor lookup first (so connections can reference by anchor)
        TMap<const UEdGraphNode*, FString> NodeToAnchor;
        UEdGraph* EventGraph = (Blueprint->UbergraphPages.Num() > 0) ? Blueprint->UbergraphPages[0] : nullptr;
        if (EventGraph != nullptr)
        {
            for (UEdGraphNode* Node : EventGraph->Nodes)
            {
                if (Node != nullptr)
                {
                    NodeToAnchor.Add(Node, DeriveAnchorForNode(Node));
                }
            }
        }

        // Determine BP status string
        FString StatusStr;
        switch (Blueprint->Status)
        {
            case BS_UpToDate:             StatusStr = TEXT("up_to_date"); break;
            case BS_UpToDateWithWarnings: StatusStr = TEXT("warnings");   break;
            case BS_Error:                StatusStr = TEXT("error");     break;
            case BS_Dirty:                StatusStr = TEXT("dirty");     break;
            case BS_Unknown:              StatusStr = TEXT("unknown");   break;
            case BS_BeingCreated:         StatusStr = TEXT("being_created"); break;
            default:                      StatusStr = TEXT("unknown");   break;
        }

        // Write JSON with TJsonWriter (auto-escapes; no double-quote bug)
        FString OutputJson;
        TSharedRef<TJsonWriter<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>> Writer =
            TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&OutputJson);

        Writer->WriteObjectStart();
        Writer->WriteValue(TEXT("ok"), true);
        Writer->WriteValue(TEXT("command"), TEXT("get_blueprint"));
        Writer->WriteValue(TEXT("path"), BlueprintPath);
        Writer->WriteValue(TEXT("parent_class"),
            Blueprint->ParentClass ? Blueprint->ParentClass->GetName() : TEXT(""));
        Writer->WriteValue(TEXT("compiled"),
            Blueprint->Status == BS_UpToDate || Blueprint->Status == BS_UpToDateWithWarnings);
        Writer->WriteValue(TEXT("status"), StatusStr);

        // anchors {anchor_name: {...}}
        Writer->WriteObjectStart(TEXT("anchors"));
        if (EventGraph != nullptr)
        {
            for (const TPair<const UEdGraphNode*, FString>& Kvp : NodeToAnchor)
            {
                Writer->WriteIdentifierPrefix(*Kvp.Value);
                WriteNodeAnchor(Writer, Kvp.Key);
            }
        }
        Writer->WriteObjectEnd();

        // connections [{from, to}]
        Writer->WriteArrayStart(TEXT("connections"));
        if (EventGraph != nullptr)
        {
            for (const UEdGraphNode* Node : EventGraph->Nodes)
            {
                if (Node == nullptr) continue;
                const FString FromAnchor = NodeToAnchor.FindRef(Node);
                for (const UEdGraphPin* Pin : Node->Pins)
                {
                    if (Pin->Direction != EGPD_Output) continue;  // emit from output side only
                    for (const UEdGraphPin* Linked : Pin->LinkedTo)
                    {
                        if (Linked == nullptr || Linked->GetOwningNode() == nullptr) continue;
                        const FString ToAnchor = NodeToAnchor.FindRef(Linked->GetOwningNode());
                        if (ToAnchor.IsEmpty()) continue;
                        Writer->WriteObjectStart();
                        Writer->WriteValue(TEXT("from"),
                            FString::Printf(TEXT("%s.%s"), *FromAnchor, *Pin->PinName.ToString()));
                        Writer->WriteValue(TEXT("to"),
                            FString::Printf(TEXT("%s.%s"), *ToAnchor, *Linked->PinName.ToString()));
                        Writer->WriteObjectEnd();
                    }
                }
            }
        }
        Writer->WriteArrayEnd();

        // variables [{name, type, subcategory}]
        Writer->WriteArrayStart(TEXT("variables"));
        for (const FBPVariableDescription& Var : Blueprint->NewVariables)
        {
            Writer->WriteObjectStart();
            Writer->WriteValue(TEXT("name"), Var.VarName.ToString());
            Writer->WriteValue(TEXT("type"), Var.VarType.PinCategory.ToString());
            if (Var.VarType.PinSubCategoryObject.IsValid())
            {
                Writer->WriteValue(TEXT("subcategory"), Var.VarType.PinSubCategoryObject->GetName());
            }
            Writer->WriteObjectEnd();
        }
        Writer->WriteArrayEnd();

        // components [{name, class}]
        Writer->WriteArrayStart(TEXT("components"));
        if (Blueprint->SimpleConstructionScript != nullptr)
        {
            for (const USCS_Node* SCSNode : Blueprint->SimpleConstructionScript->GetAllNodes())
            {
                if (SCSNode == nullptr || SCSNode->ComponentClass == nullptr) continue;
                Writer->WriteObjectStart();
                Writer->WriteValue(TEXT("name"), SCSNode->GetVariableName().ToString());
                Writer->WriteValue(TEXT("class"), SCSNode->ComponentClass->GetName());
                Writer->WriteObjectEnd();
            }
        }
        Writer->WriteArrayEnd();

        Writer->WriteObjectEnd();
        Writer->Close();

        return OutputJson + TEXT("\n");
    }

    /**
     * Spawn a Blueprint instance into the current level. MUST run on the game thread.
     * Returns a complete JSON response line (with trailing \n).
     */
    FString SpawnActorOnGameThread(
        const FString& BlueprintPath,
        float LocX, float LocY, float LocZ)
    {
        check(IsInGameThread());

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr)
        {
            return JsonError(TEXT("spawn_actor"), TEXT("blueprint_not_found"), BlueprintPath);
        }

        // BP must be compiled — GeneratedClass is null otherwise
        UClass* BPClass = Blueprint->GeneratedClass;
        if (BPClass == nullptr)
        {
            return JsonError(TEXT("spawn_actor"), TEXT("no_generated_class"),
                TEXT("Blueprint must be compiled first (call compile_blueprint)"));
        }

        // Must be an Actor subclass to be spawnable
        if (!BPClass->IsChildOf(AActor::StaticClass()))
        {
            return JsonError(TEXT("spawn_actor"), TEXT("not_actor_subclass"),
                BPClass->GetName());
        }

        // Use modern EditorActorSubsystem (UEditorLevelLibrary is deprecated in 5.x)
        if (GEditor == nullptr)
        {
            return JsonError(TEXT("spawn_actor"), TEXT("no_editor"), TEXT("GEditor null"));
        }
        UEditorActorSubsystem* ActorSubsystem = GEditor->GetEditorSubsystem<UEditorActorSubsystem>();
        if (ActorSubsystem == nullptr)
        {
            return JsonError(TEXT("spawn_actor"), TEXT("no_actor_subsystem"), TEXT("nullptr"));
        }

        const FVector Location(LocX, LocY, LocZ);
        const FRotator Rotation = FRotator::ZeroRotator;

        AActor* SpawnedActor = ActorSubsystem->SpawnActorFromClass(BPClass, Location, Rotation);
        if (SpawnedActor == nullptr)
        {
            return JsonError(TEXT("spawn_actor"), TEXT("spawn_failed"), BlueprintPath);
        }

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"spawn_actor\",\"blueprint_path\":%s,\"actor_name\":%s,\"location\":[%f,%f,%f]}\n"),
            *EscapeJsonString(BlueprintPath),
            *EscapeJsonString(SpawnedActor->GetName()),
            LocX, LocY, LocZ);
    }

    /**
     * Compile a Blueprint. MUST run on the game thread.
     * Returns a complete JSON response line (with trailing \n).
     *
     * Returns status as one of:
     *   "up_to_date"  - compile succeeded, no warnings (BS_UpToDate)
     *   "warnings"    - compile succeeded with warnings (BS_UpToDateWithWarnings)
     *   "error"       - compile failed (BS_Error)
     *   "dirty"       - compile didn't take effect (BS_Dirty post-compile is unusual)
     *   "unknown"     - unrecognized status
     *
     * For detailed compile errors / warnings, check the UE Editor's Message Log
     * (Window → Developer Tools → Message Log → "Blueprint Log" tab).
     */
    FString CompileBlueprintOnGameThread(const FString& BlueprintPath)
    {
        check(IsInGameThread());

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr)
        {
            return JsonError(TEXT("compile_blueprint"), TEXT("blueprint_not_found"), BlueprintPath);
        }

        // Trigger compile (this is synchronous on the game thread)
        FKismetEditorUtilities::CompileBlueprint(Blueprint, EBlueprintCompileOptions::None);

        FString StatusStr;
        bool bOK = false;
        switch (Blueprint->Status)
        {
            case BS_UpToDate:             StatusStr = TEXT("up_to_date"); bOK = true; break;
            case BS_UpToDateWithWarnings: StatusStr = TEXT("warnings");   bOK = true; break;
            case BS_Error:                StatusStr = TEXT("error");      bOK = false; break;
            case BS_Dirty:                StatusStr = TEXT("dirty");      bOK = false; break;
            case BS_Unknown:              StatusStr = TEXT("unknown");    bOK = false; break;
            case BS_BeingCreated:         StatusStr = TEXT("being_created"); bOK = false; break;
            default:                      StatusStr = TEXT("unknown");    bOK = false; break;
        }

        // Save after compile so the compiled state persists
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);

        if (bOK)
        {
            return FString::Printf(
                TEXT("{\"ok\":true,\"command\":\"compile_blueprint\",\"status\":%s,\"saved\":%s}\n"),
                *EscapeJsonString(StatusStr),
                bSaved ? TEXT("true") : TEXT("false"));
        }
        else
        {
            return FString::Printf(
                TEXT("{\"ok\":false,\"command\":\"compile_blueprint\",\"error\":\"compile_failed\",\"status\":%s,\"hint\":\"See UE Editor Message Log → Blueprint Log tab for details.\",\"saved\":%s}\n"),
                *EscapeJsonString(StatusStr),
                bSaved ? TEXT("true") : TEXT("false"));
        }
    }

    /**
     * Connect two pins. MUST run on the game thread.
     * Returns a complete JSON response line (with trailing \n).
     */
    FString ConnectPinsOnGameThread(
        const FString& BlueprintPath,
        const FString& FromPinRef,
        const FString& ToPinRef)
    {
        check(IsInGameThread());

        // 1. Load BP + EventGraph
        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr)
        {
            return JsonError(TEXT("connect_pins"), TEXT("blueprint_not_found"), BlueprintPath);
        }
        if (Blueprint->UbergraphPages.Num() == 0)
        {
            return JsonError(TEXT("connect_pins"), TEXT("no_event_graph"), BlueprintPath);
        }
        UEdGraph* EventGraph = Blueprint->UbergraphPages[0];

        // 2. Resolve both pins (errors come back as ready-to-return JSON)
        FString FromErrJson, ToErrJson;
        UEdGraphPin* FromPin = ResolvePinRef(EventGraph, FromPinRef, TEXT("connect_pins"), FromErrJson);
        if (FromPin == nullptr) return FromErrJson;
        UEdGraphPin* ToPin = ResolvePinRef(EventGraph, ToPinRef, TEXT("connect_pins"), ToErrJson);
        if (ToPin == nullptr) return ToErrJson;

        // 3. Validate via schema before attempting
        const UEdGraphSchema_K2* Schema = Cast<UEdGraphSchema_K2>(EventGraph->GetSchema());
        if (Schema == nullptr)
        {
            return JsonError(TEXT("connect_pins"), TEXT("schema_not_k2"), BlueprintPath);
        }

        const FPinConnectionResponse CanConnect = Schema->CanCreateConnection(FromPin, ToPin);
        if (CanConnect.Response == CONNECT_RESPONSE_DISALLOW)
        {
            return JsonError(TEXT("connect_pins"), TEXT("incompatible_pins"),
                CanConnect.Message.ToString());
        }

        // 4. Actually connect
        const bool bConnected = Schema->TryCreateConnection(FromPin, ToPin);
        if (!bConnected)
        {
            return JsonError(TEXT("connect_pins"), TEXT("connection_failed"),
                FString::Printf(TEXT("%s -> %s"), *FromPinRef, *ToPinRef));
        }

        // 5. Mark + save
        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);

        // 6. Response (echo back the canonical pin refs UE has)
        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"connect_pins\",\"from\":%s,\"to\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(FromPinRef),
            *EscapeJsonString(ToPinRef),
            bSaved ? TEXT("true") : TEXT("false"));
    }

    /**
     * Create a Blueprint asset. MUST run on the game thread.
     * Returns a complete JSON response line (with trailing \n).
     */
    FString CreateBlueprintOnGameThread(const FString& Name, const FString& ParentClassStr, const FString& Path)
    {
        check(IsInGameThread());

        UClass* ParentClass = ResolveParentClass(ParentClassStr);
        if (ParentClass == nullptr)
        {
            return JsonError(TEXT("create_blueprint"), TEXT("unknown_parent_class"), ParentClassStr);
        }

        const FString FullAssetPath = Path / Name;

        if (UEditorAssetLibrary::DoesAssetExist(FullAssetPath))
        {
            return JsonError(TEXT("create_blueprint"), TEXT("asset_exists"), FullAssetPath);
        }

        UBlueprintFactory* Factory = NewObject<UBlueprintFactory>();
        Factory->ParentClass = ParentClass;

        FAssetToolsModule& AssetToolsModule =
            FModuleManager::LoadModuleChecked<FAssetToolsModule>("AssetTools");
        UObject* NewAsset = AssetToolsModule.Get().CreateAsset(
            Name, Path, UBlueprint::StaticClass(), Factory);

        if (NewAsset == nullptr)
        {
            return JsonError(TEXT("create_blueprint"), TEXT("creation_failed"), FullAssetPath);
        }

        // Persist so the asset survives editor restart.
        const bool bSaved = UEditorAssetLibrary::SaveAsset(FullAssetPath, /*bOnlyIfIsDirty*/ false);
        if (!bSaved)
        {
            UE_LOG(LogBlueprintMCP_TCP, Warning,
                TEXT("create_blueprint: asset created but save failed (%s)"), *FullAssetPath);
        }

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"create_blueprint\",\"blueprint_path\":%s,\"parent_class\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(FullAssetPath),
            *EscapeJsonString(ParentClass->GetName()),
            bSaved ? TEXT("true") : TEXT("false"));
    }
}

FTCPServerRunnable::FTCPServerRunnable(int32 InPort)
    : Port(InPort)
{
}

FTCPServerRunnable::~FTCPServerRunnable()
{
    if (ListenSocket != nullptr)
    {
        ListenSocket->Close();
        ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM)->DestroySocket(ListenSocket);
        ListenSocket = nullptr;
    }
}

bool FTCPServerRunnable::Init()
{
    const FIPv4Endpoint Endpoint(FIPv4Address::Any, static_cast<uint16>(Port));
    ListenSocket = FTcpSocketBuilder(TEXT("BlueprintMCP_Listener"))
                      .AsReusable()
                      .BoundToEndpoint(Endpoint)
                      .Listening(8);

    if (ListenSocket == nullptr)
    {
        UE_LOG(LogBlueprintMCP_TCP, Error, TEXT("Failed to create listen socket on port %d"), Port);
        return false;
    }

    UE_LOG(LogBlueprintMCP_TCP, Log, TEXT("TCP server listening on 0.0.0.0:%d"), Port);
    return true;
}

uint32 FTCPServerRunnable::Run()
{
    ISocketSubsystem* SocketSubsystem = ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM);

    while (!bShouldStop)
    {
        bool bHasPending = false;
        if (!ListenSocket->HasPendingConnection(bHasPending))
        {
            FPlatformProcess::Sleep(0.05f);
            continue;
        }

        if (!bHasPending)
        {
            FPlatformProcess::Sleep(0.05f);
            continue;
        }

        FSocket* ClientSocket = ListenSocket->Accept(TEXT("BlueprintMCP_Client"));
        if (ClientSocket == nullptr)
        {
            continue;
        }

        HandleClient(ClientSocket);

        ClientSocket->Close();
        SocketSubsystem->DestroySocket(ClientSocket);
    }

    UE_LOG(LogBlueprintMCP_TCP, Log, TEXT("TCP server loop exited"));
    return 0;
}

void FTCPServerRunnable::Stop()
{
    bShouldStop = true;
}

void FTCPServerRunnable::HandleClient(FSocket* ClientSocket)
{
    uint8 Buffer[kReceiveBufferSize] = {};
    int32 BytesRead = 0;

    // v0 simple: read up to one buffer (assumes one line per connection).
    // Improve later if needed.
    if (!ClientSocket->Recv(Buffer, kReceiveBufferSize, BytesRead, ESocketReceiveFlags::None))
    {
        UE_LOG(LogBlueprintMCP_TCP, Warning, TEXT("Client recv failed"));
        return;
    }

    if (BytesRead <= 0)
    {
        return;
    }

    const FString JsonLine(BytesRead, reinterpret_cast<const ANSICHAR*>(Buffer));
    UE_LOG(LogBlueprintMCP_TCP, Verbose, TEXT("Received: %s"), *JsonLine);

    const FString Response = DispatchCommand(JsonLine);
    const FTCHARToUTF8 ResponseUtf8(*Response);
    int32 BytesSent = 0;
    ClientSocket->Send(reinterpret_cast<const uint8*>(ResponseUtf8.Get()), ResponseUtf8.Length(), BytesSent);
    UE_LOG(LogBlueprintMCP_TCP, Verbose, TEXT("Sent: %s"), *Response);
}

FString FTCPServerRunnable::DispatchCommand(const FString& JsonCommandLine)
{
    TSharedPtr<FJsonObject> JsonObject;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonCommandLine);

    if (!FJsonSerializer::Deserialize(Reader, JsonObject) || !JsonObject.IsValid())
    {
        return TEXT("{\"ok\":false,\"error\":\"invalid_json\"}\n");
    }

    FString Command;
    if (!JsonObject->TryGetStringField(TEXT("command"), Command))
    {
        return TEXT("{\"ok\":false,\"error\":\"missing_command_field\"}\n");
    }

    // --- ping (Spike A1) ---
    if (Command.Equals(TEXT("ping"), ESearchCase::IgnoreCase))
    {
        const FString Timestamp = FDateTime::UtcNow().ToIso8601();
        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"ping\",\"version\":\"0.0.1\",\"timestamp\":\"%s\"}\n"),
            *Timestamp);
    }

    // --- create_blueprint (Spike B1) ---
    if (Command.Equals(TEXT("create_blueprint"), ESearchCase::IgnoreCase))
    {
        FString Name;
        if (!JsonObject->TryGetStringField(TEXT("name"), Name) || Name.IsEmpty())
        {
            return JsonError(TEXT("create_blueprint"), TEXT("missing_field"), TEXT("name"));
        }

        FString ParentClassStr;
        if (!JsonObject->TryGetStringField(TEXT("parent_class"), ParentClassStr) || ParentClassStr.IsEmpty())
        {
            ParentClassStr = TEXT("Actor");
        }

        FString Path;
        if (!JsonObject->TryGetStringField(TEXT("path"), Path) || Path.IsEmpty())
        {
            Path = TEXT("/Game/Blueprints");
        }

        // Marshal to game thread synchronously (TPromise/TFuture keeps captures
        // alive via shared state even if we time out before the lambda runs).
        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();

        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Name, ParentClassStr, Path]() mutable
            {
                FString Result = CreateBlueprintOnGameThread(Name, ParentClassStr, Path);
                Promise.SetValue(MoveTemp(Result));
            });

        const FTimespan Timeout = FTimespan::FromSeconds(kGameThreadTimeoutSeconds);
        if (!Future.WaitFor(Timeout))
        {
            UE_LOG(LogBlueprintMCP_TCP, Error, TEXT("create_blueprint timed out after %ds"), kGameThreadTimeoutSeconds);
            return JsonError(TEXT("create_blueprint"), TEXT("game_thread_timeout"));
        }
        return Future.Get();
    }

    // --- add_component (Spike B7) ---
    if (Command.Equals(TEXT("add_component"), ESearchCase::IgnoreCase))
    {
        FString Blueprint, ComponentClass, ComponentName;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(TEXT("add_component"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("component_class"), ComponentClass) || ComponentClass.IsEmpty())
            return JsonError(TEXT("add_component"), TEXT("missing_field"), TEXT("component_class"));
        if (!JsonObject->TryGetStringField(TEXT("name"), ComponentName) || ComponentName.IsEmpty())
            return JsonError(TEXT("add_component"), TEXT("missing_field"), TEXT("name"));

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, ComponentClass, ComponentName]() mutable
            {
                Promise.SetValue(AddComponentOnGameThread(Blueprint, ComponentClass, ComponentName));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("add_component"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- add_custom_event (Spike B8) ---
    if (Command.Equals(TEXT("add_custom_event"), ESearchCase::IgnoreCase))
    {
        FString Blueprint, EventName, AnchorName;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(TEXT("add_custom_event"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("event_name"), EventName) || EventName.IsEmpty())
            return JsonError(TEXT("add_custom_event"), TEXT("missing_field"), TEXT("event_name"));
        if (!JsonObject->TryGetStringField(TEXT("anchor_name"), AnchorName) || AnchorName.IsEmpty())
            return JsonError(TEXT("add_custom_event"), TEXT("missing_field"), TEXT("anchor_name"));

        int32 PosX = 0, PosY = 0;
        JsonObject->TryGetNumberField(TEXT("position_x"), PosX);
        JsonObject->TryGetNumberField(TEXT("position_y"), PosY);

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, EventName, AnchorName, PosX, PosY]() mutable
            {
                Promise.SetValue(AddCustomEventOnGameThread(Blueprint, EventName, AnchorName, PosX, PosY));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("add_custom_event"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- add_variable (Spike B9) ---
    if (Command.Equals(TEXT("add_variable"), ESearchCase::IgnoreCase))
    {
        FString Blueprint, Name, VarType, DefaultValue;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(TEXT("add_variable"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("name"), Name) || Name.IsEmpty())
            return JsonError(TEXT("add_variable"), TEXT("missing_field"), TEXT("name"));
        if (!JsonObject->TryGetStringField(TEXT("variable_type"), VarType) || VarType.IsEmpty())
            return JsonError(TEXT("add_variable"), TEXT("missing_field"), TEXT("variable_type"));
        JsonObject->TryGetStringField(TEXT("default_value"), DefaultValue);  // optional

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, Name, VarType, DefaultValue]() mutable
            {
                Promise.SetValue(AddVariableOnGameThread(Blueprint, Name, VarType, DefaultValue));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("add_variable"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- add_variable_get / add_variable_set (Spike B10) ---
    if (Command.Equals(TEXT("add_variable_get"), ESearchCase::IgnoreCase) ||
        Command.Equals(TEXT("add_variable_set"), ESearchCase::IgnoreCase))
    {
        const bool bIsSet = Command.Equals(TEXT("add_variable_set"), ESearchCase::IgnoreCase);
        const TCHAR* CmdName = bIsSet ? TEXT("add_variable_set") : TEXT("add_variable_get");

        FString Blueprint, VariableName, AnchorName;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(CmdName, TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("variable_name"), VariableName) || VariableName.IsEmpty())
            return JsonError(CmdName, TEXT("missing_field"), TEXT("variable_name"));
        if (!JsonObject->TryGetStringField(TEXT("anchor_name"), AnchorName) || AnchorName.IsEmpty())
            return JsonError(CmdName, TEXT("missing_field"), TEXT("anchor_name"));

        int32 PosX = 0, PosY = 0;
        JsonObject->TryGetNumberField(TEXT("position_x"), PosX);
        JsonObject->TryGetNumberField(TEXT("position_y"), PosY);

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, VariableName, AnchorName, PosX, PosY, bIsSet]() mutable
            {
                Promise.SetValue(AddVariableRefOnGameThread(Blueprint, VariableName, AnchorName, PosX, PosY, bIsSet));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(CmdName, TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- add_function (v5) ---
    if (Command.Equals(TEXT("add_function"), ESearchCase::IgnoreCase))
    {
        FString Blueprint, FunctionName;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(TEXT("add_function"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("name"), FunctionName) || FunctionName.IsEmpty())
            return JsonError(TEXT("add_function"), TEXT("missing_field"), TEXT("name"));

        TPromise<FString> Promise; TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, FunctionName]() mutable
            { Promise.SetValue(AddFunctionOnGameThread(Blueprint, FunctionName)); });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("add_function"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- call_blueprint_function (v5) ---
    if (Command.Equals(TEXT("call_blueprint_function"), ESearchCase::IgnoreCase))
    {
        FString Blueprint, TargetClass, FunctionName, AnchorName;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(TEXT("call_blueprint_function"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("target_class"), TargetClass) || TargetClass.IsEmpty())
            return JsonError(TEXT("call_blueprint_function"), TEXT("missing_field"), TEXT("target_class"));
        if (!JsonObject->TryGetStringField(TEXT("function_name"), FunctionName) || FunctionName.IsEmpty())
            return JsonError(TEXT("call_blueprint_function"), TEXT("missing_field"), TEXT("function_name"));
        if (!JsonObject->TryGetStringField(TEXT("anchor_name"), AnchorName) || AnchorName.IsEmpty())
            return JsonError(TEXT("call_blueprint_function"), TEXT("missing_field"), TEXT("anchor_name"));
        int32 PosX = 0, PosY = 0;
        JsonObject->TryGetNumberField(TEXT("position_x"), PosX);
        JsonObject->TryGetNumberField(TEXT("position_y"), PosY);

        TPromise<FString> Promise; TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, TargetClass, FunctionName, AnchorName, PosX, PosY]() mutable
            { Promise.SetValue(CallBlueprintFunctionOnGameThread(Blueprint, TargetClass, FunctionName, AnchorName, PosX, PosY)); });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("call_blueprint_function"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- create_input_action (v5) ---
    if (Command.Equals(TEXT("create_input_action"), ESearchCase::IgnoreCase))
    {
        FString Name, ValueType, Path;
        if (!JsonObject->TryGetStringField(TEXT("name"), Name) || Name.IsEmpty())
            return JsonError(TEXT("create_input_action"), TEXT("missing_field"), TEXT("name"));
        if (!JsonObject->TryGetStringField(TEXT("value_type"), ValueType) || ValueType.IsEmpty())
            ValueType = TEXT("Boolean");
        if (!JsonObject->TryGetStringField(TEXT("path"), Path) || Path.IsEmpty())
            Path = TEXT("/Game/Input/Actions");

        TPromise<FString> Promise; TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Name, ValueType, Path]() mutable
            { Promise.SetValue(CreateInputActionOnGameThread(Name, ValueType, Path)); });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("create_input_action"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- create_input_mapping_context (v5) ---
    if (Command.Equals(TEXT("create_input_mapping_context"), ESearchCase::IgnoreCase))
    {
        FString Name, Path;
        if (!JsonObject->TryGetStringField(TEXT("name"), Name) || Name.IsEmpty())
            return JsonError(TEXT("create_input_mapping_context"), TEXT("missing_field"), TEXT("name"));
        if (!JsonObject->TryGetStringField(TEXT("path"), Path) || Path.IsEmpty())
            Path = TEXT("/Game/Input");

        TPromise<FString> Promise; TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Name, Path]() mutable
            { Promise.SetValue(CreateInputMappingContextOnGameThread(Name, Path)); });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("create_input_mapping_context"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- add_mapping_to_imc (v5) ---
    if (Command.Equals(TEXT("add_mapping_to_imc"), ESearchCase::IgnoreCase))
    {
        FString IMCPath, ActionPath, KeyName;
        if (!JsonObject->TryGetStringField(TEXT("imc_path"), IMCPath) || IMCPath.IsEmpty())
            return JsonError(TEXT("add_mapping_to_imc"), TEXT("missing_field"), TEXT("imc_path"));
        if (!JsonObject->TryGetStringField(TEXT("action_path"), ActionPath) || ActionPath.IsEmpty())
            return JsonError(TEXT("add_mapping_to_imc"), TEXT("missing_field"), TEXT("action_path"));
        if (!JsonObject->TryGetStringField(TEXT("key"), KeyName) || KeyName.IsEmpty())
            return JsonError(TEXT("add_mapping_to_imc"), TEXT("missing_field"), TEXT("key"));

        TPromise<FString> Promise; TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), IMCPath, ActionPath, KeyName]() mutable
            { Promise.SetValue(AddMappingToImcOnGameThread(IMCPath, ActionPath, KeyName)); });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("add_mapping_to_imc"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- add_enhanced_input_node (v5) ---
    if (Command.Equals(TEXT("add_enhanced_input_node"), ESearchCase::IgnoreCase))
    {
        FString Blueprint, ActionPath, AnchorName;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(TEXT("add_enhanced_input_node"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("action_path"), ActionPath) || ActionPath.IsEmpty())
            return JsonError(TEXT("add_enhanced_input_node"), TEXT("missing_field"), TEXT("action_path"));
        if (!JsonObject->TryGetStringField(TEXT("anchor_name"), AnchorName) || AnchorName.IsEmpty())
            return JsonError(TEXT("add_enhanced_input_node"), TEXT("missing_field"), TEXT("anchor_name"));
        int32 PosX = 0, PosY = 0;
        JsonObject->TryGetNumberField(TEXT("position_x"), PosX);
        JsonObject->TryGetNumberField(TEXT("position_y"), PosY);

        TPromise<FString> Promise; TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, ActionPath, AnchorName, PosX, PosY]() mutable
            { Promise.SetValue(AddEnhancedInputNodeOnGameThread(Blueprint, ActionPath, AnchorName, PosX, PosY)); });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("add_enhanced_input_node"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- add_macro (v4) ---
    if (Command.Equals(TEXT("add_macro"), ESearchCase::IgnoreCase))
    {
        FString Blueprint, MacroType, AnchorName;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(TEXT("add_macro"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("macro_type"), MacroType) || MacroType.IsEmpty())
            return JsonError(TEXT("add_macro"), TEXT("missing_field"), TEXT("macro_type"));
        if (!JsonObject->TryGetStringField(TEXT("anchor_name"), AnchorName) || AnchorName.IsEmpty())
            return JsonError(TEXT("add_macro"), TEXT("missing_field"), TEXT("anchor_name"));
        int32 PosX = 0, PosY = 0;
        JsonObject->TryGetNumberField(TEXT("position_x"), PosX);
        JsonObject->TryGetNumberField(TEXT("position_y"), PosY);

        TPromise<FString> Promise; TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, MacroType, AnchorName, PosX, PosY]() mutable
            { Promise.SetValue(AddMacroOnGameThread(Blueprint, MacroType, AnchorName, PosX, PosY)); });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("add_macro"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- add_self_reference (v4) ---
    if (Command.Equals(TEXT("add_self_reference"), ESearchCase::IgnoreCase))
    {
        FString Blueprint, AnchorName;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(TEXT("add_self_reference"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("anchor_name"), AnchorName) || AnchorName.IsEmpty())
            return JsonError(TEXT("add_self_reference"), TEXT("missing_field"), TEXT("anchor_name"));
        int32 PosX = 0, PosY = 0;
        JsonObject->TryGetNumberField(TEXT("position_x"), PosX);
        JsonObject->TryGetNumberField(TEXT("position_y"), PosY);

        TPromise<FString> Promise; TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, AnchorName, PosX, PosY]() mutable
            { Promise.SetValue(AddSelfReferenceOnGameThread(Blueprint, AnchorName, PosX, PosY)); });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("add_self_reference"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- add_input_key (v4) ---
    if (Command.Equals(TEXT("add_input_key"), ESearchCase::IgnoreCase))
    {
        FString Blueprint, KeyName, AnchorName;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(TEXT("add_input_key"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("key"), KeyName) || KeyName.IsEmpty())
            return JsonError(TEXT("add_input_key"), TEXT("missing_field"), TEXT("key"));
        if (!JsonObject->TryGetStringField(TEXT("anchor_name"), AnchorName) || AnchorName.IsEmpty())
            return JsonError(TEXT("add_input_key"), TEXT("missing_field"), TEXT("anchor_name"));
        int32 PosX = 0, PosY = 0;
        JsonObject->TryGetNumberField(TEXT("position_x"), PosX);
        JsonObject->TryGetNumberField(TEXT("position_y"), PosY);

        TPromise<FString> Promise; TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, KeyName, AnchorName, PosX, PosY]() mutable
            { Promise.SetValue(AddInputKeyOnGameThread(Blueprint, KeyName, AnchorName, PosX, PosY)); });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("add_input_key"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- delete_node (v4) ---
    if (Command.Equals(TEXT("delete_node"), ESearchCase::IgnoreCase))
    {
        FString Blueprint, AnchorName;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(TEXT("delete_node"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("anchor_name"), AnchorName) || AnchorName.IsEmpty())
            return JsonError(TEXT("delete_node"), TEXT("missing_field"), TEXT("anchor_name"));

        TPromise<FString> Promise; TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, AnchorName]() mutable
            { Promise.SetValue(DeleteNodeOnGameThread(Blueprint, AnchorName)); });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("delete_node"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- disconnect_pins (v4) ---
    if (Command.Equals(TEXT("disconnect_pins"), ESearchCase::IgnoreCase))
    {
        FString Blueprint, FromPin, ToPin;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(TEXT("disconnect_pins"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("from_pin"), FromPin) || FromPin.IsEmpty())
            return JsonError(TEXT("disconnect_pins"), TEXT("missing_field"), TEXT("from_pin"));
        if (!JsonObject->TryGetStringField(TEXT("to_pin"), ToPin) || ToPin.IsEmpty())
            return JsonError(TEXT("disconnect_pins"), TEXT("missing_field"), TEXT("to_pin"));

        TPromise<FString> Promise; TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, FromPin, ToPin]() mutable
            { Promise.SetValue(DisconnectPinsOnGameThread(Blueprint, FromPin, ToPin)); });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("disconnect_pins"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- add_branch (v3) ---
    if (Command.Equals(TEXT("add_branch"), ESearchCase::IgnoreCase))
    {
        FString Blueprint, AnchorName;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(TEXT("add_branch"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("anchor_name"), AnchorName) || AnchorName.IsEmpty())
            return JsonError(TEXT("add_branch"), TEXT("missing_field"), TEXT("anchor_name"));

        int32 PosX = 0, PosY = 0;
        JsonObject->TryGetNumberField(TEXT("position_x"), PosX);
        JsonObject->TryGetNumberField(TEXT("position_y"), PosY);

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, AnchorName, PosX, PosY]() mutable
            {
                Promise.SetValue(AddBranchOnGameThread(Blueprint, AnchorName, PosX, PosY));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("add_branch"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- add_cast (v3) ---
    if (Command.Equals(TEXT("add_cast"), ESearchCase::IgnoreCase))
    {
        FString Blueprint, TargetClass, AnchorName;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(TEXT("add_cast"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("target_class"), TargetClass) || TargetClass.IsEmpty())
            return JsonError(TEXT("add_cast"), TEXT("missing_field"), TEXT("target_class"));
        if (!JsonObject->TryGetStringField(TEXT("anchor_name"), AnchorName) || AnchorName.IsEmpty())
            return JsonError(TEXT("add_cast"), TEXT("missing_field"), TEXT("anchor_name"));

        int32 PosX = 0, PosY = 0;
        JsonObject->TryGetNumberField(TEXT("position_x"), PosX);
        JsonObject->TryGetNumberField(TEXT("position_y"), PosY);

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, TargetClass, AnchorName, PosX, PosY]() mutable
            {
                Promise.SetValue(AddCastOnGameThread(Blueprint, TargetClass, AnchorName, PosX, PosY));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("add_cast"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- get_blueprint (v2) ---
    if (Command.Equals(TEXT("get_blueprint"), ESearchCase::IgnoreCase))
    {
        FString Name;
        if (!JsonObject->TryGetStringField(TEXT("name"), Name) || Name.IsEmpty())
        {
            return JsonError(TEXT("get_blueprint"), TEXT("missing_field"), TEXT("name"));
        }
        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Name]() mutable
            {
                Promise.SetValue(GetBlueprintOnGameThread(Name));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("get_blueprint"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- spawn_actor (Spike B6) ---
    if (Command.Equals(TEXT("spawn_actor"), ESearchCase::IgnoreCase))
    {
        FString Blueprint;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
        {
            return JsonError(TEXT("spawn_actor"), TEXT("missing_field"), TEXT("blueprint"));
        }
        double LocX = 0.0, LocY = 0.0, LocZ = 0.0;
        JsonObject->TryGetNumberField(TEXT("location_x"), LocX);
        JsonObject->TryGetNumberField(TEXT("location_y"), LocY);
        JsonObject->TryGetNumberField(TEXT("location_z"), LocZ);

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();

        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, LocX, LocY, LocZ]() mutable
            {
                FString Result = SpawnActorOnGameThread(Blueprint,
                    static_cast<float>(LocX), static_cast<float>(LocY), static_cast<float>(LocZ));
                Promise.SetValue(MoveTemp(Result));
            });

        const FTimespan Timeout = FTimespan::FromSeconds(kGameThreadTimeoutSeconds);
        if (!Future.WaitFor(Timeout))
        {
            UE_LOG(LogBlueprintMCP_TCP, Error, TEXT("spawn_actor timed out"));
            return JsonError(TEXT("spawn_actor"), TEXT("game_thread_timeout"));
        }
        return Future.Get();
    }

    // --- compile_blueprint (Spike B5) ---
    if (Command.Equals(TEXT("compile_blueprint"), ESearchCase::IgnoreCase))
    {
        FString Name;
        if (!JsonObject->TryGetStringField(TEXT("name"), Name) || Name.IsEmpty())
        {
            return JsonError(TEXT("compile_blueprint"), TEXT("missing_field"), TEXT("name"));
        }

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();

        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Name]() mutable
            {
                FString Result = CompileBlueprintOnGameThread(Name);
                Promise.SetValue(MoveTemp(Result));
            });

        // Compile can be slower than other ops; give it 30s
        const FTimespan Timeout = FTimespan::FromSeconds(30);
        if (!Future.WaitFor(Timeout))
        {
            UE_LOG(LogBlueprintMCP_TCP, Error, TEXT("compile_blueprint timed out after 30s"));
            return JsonError(TEXT("compile_blueprint"), TEXT("game_thread_timeout"));
        }
        return Future.Get();
    }

    // --- connect_pins (Spike B4) ---
    if (Command.Equals(TEXT("connect_pins"), ESearchCase::IgnoreCase))
    {
        FString Blueprint;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
        {
            return JsonError(TEXT("connect_pins"), TEXT("missing_field"), TEXT("blueprint"));
        }
        FString FromPin;
        if (!JsonObject->TryGetStringField(TEXT("from_pin"), FromPin) || FromPin.IsEmpty())
        {
            return JsonError(TEXT("connect_pins"), TEXT("missing_field"), TEXT("from_pin"));
        }
        FString ToPin;
        if (!JsonObject->TryGetStringField(TEXT("to_pin"), ToPin) || ToPin.IsEmpty())
        {
            return JsonError(TEXT("connect_pins"), TEXT("missing_field"), TEXT("to_pin"));
        }

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();

        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, FromPin, ToPin]() mutable
            {
                FString Result = ConnectPinsOnGameThread(Blueprint, FromPin, ToPin);
                Promise.SetValue(MoveTemp(Result));
            });

        const FTimespan Timeout = FTimespan::FromSeconds(kGameThreadTimeoutSeconds);
        if (!Future.WaitFor(Timeout))
        {
            UE_LOG(LogBlueprintMCP_TCP, Error, TEXT("connect_pins timed out after %ds"), kGameThreadTimeoutSeconds);
            return JsonError(TEXT("connect_pins"), TEXT("game_thread_timeout"));
        }
        return Future.Get();
    }

    // --- set_pin_default (Spike B3) ---
    if (Command.Equals(TEXT("set_pin_default"), ESearchCase::IgnoreCase))
    {
        FString Blueprint;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
        {
            return JsonError(TEXT("set_pin_default"), TEXT("missing_field"), TEXT("blueprint"));
        }
        FString PinRef;
        if (!JsonObject->TryGetStringField(TEXT("pin_ref"), PinRef) || PinRef.IsEmpty())
        {
            return JsonError(TEXT("set_pin_default"), TEXT("missing_field"), TEXT("pin_ref"));
        }
        FString Value;
        // value can legitimately be empty string, but field must be present
        if (!JsonObject->HasField(TEXT("value")))
        {
            return JsonError(TEXT("set_pin_default"), TEXT("missing_field"), TEXT("value"));
        }
        JsonObject->TryGetStringField(TEXT("value"), Value);

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();

        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, PinRef, Value]() mutable
            {
                FString Result = SetPinDefaultOnGameThread(Blueprint, PinRef, Value);
                Promise.SetValue(MoveTemp(Result));
            });

        const FTimespan Timeout = FTimespan::FromSeconds(kGameThreadTimeoutSeconds);
        if (!Future.WaitFor(Timeout))
        {
            UE_LOG(LogBlueprintMCP_TCP, Error, TEXT("set_pin_default timed out after %ds"), kGameThreadTimeoutSeconds);
            return JsonError(TEXT("set_pin_default"), TEXT("game_thread_timeout"));
        }
        return Future.Get();
    }

    // --- add_node (Spike B2) ---
    if (Command.Equals(TEXT("add_node"), ESearchCase::IgnoreCase))
    {
        FString Blueprint;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
        {
            return JsonError(TEXT("add_node"), TEXT("missing_field"), TEXT("blueprint"));
        }

        FString NodeType;
        if (!JsonObject->TryGetStringField(TEXT("node_type"), NodeType) || NodeType.IsEmpty())
        {
            return JsonError(TEXT("add_node"), TEXT("missing_field"), TEXT("node_type"));
        }

        FString AnchorName;
        if (!JsonObject->TryGetStringField(TEXT("anchor_name"), AnchorName) || AnchorName.IsEmpty())
        {
            return JsonError(TEXT("add_node"), TEXT("missing_field"), TEXT("anchor_name"));
        }

        int32 PosX = 0;
        int32 PosY = 0;
        JsonObject->TryGetNumberField(TEXT("position_x"), PosX);
        JsonObject->TryGetNumberField(TEXT("position_y"), PosY);

        // Marshal to game thread (same TPromise/TFuture pattern as create_blueprint)
        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();

        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, NodeType, AnchorName, PosX, PosY]() mutable
            {
                FString Result = AddNodeOnGameThread(Blueprint, NodeType, AnchorName, PosX, PosY);
                Promise.SetValue(MoveTemp(Result));
            });

        const FTimespan Timeout = FTimespan::FromSeconds(kGameThreadTimeoutSeconds);
        if (!Future.WaitFor(Timeout))
        {
            UE_LOG(LogBlueprintMCP_TCP, Error, TEXT("add_node timed out after %ds"), kGameThreadTimeoutSeconds);
            return JsonError(TEXT("add_node"), TEXT("game_thread_timeout"));
        }
        return Future.Get();
    }

    return FString::Printf(
        TEXT("{\"ok\":false,\"error\":\"unknown_command\",\"command\":%s}\n"),
        *EscapeJsonString(Command));
}
