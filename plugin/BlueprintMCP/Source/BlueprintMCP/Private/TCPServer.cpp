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

// v6 — IMC subscribe chain
#include "EnhancedInputSubsystems.h"   // UEnhancedInputLocalPlayerSubsystem
#include "Kismet/GameplayStatics.h"    // UGameplayStatics for GetPlayerController
// USubsystemBlueprintLibrary — this is what UK2Node_GetSubsystemFromPC expands to at BP compile time.
// We call the BP function directly to avoid depending on the (non-exported) K2Node.
#include "Subsystems/SubsystemBlueprintLibrary.h"

// v8 — PIE control + input simulation
#include "Editor/EditorEngine.h"        // FRequestPlaySessionParams
#include "GameFramework/PlayerController.h"  // already in v3, but explicit
#include "Engine/World.h"                // UWorld for PIE

// v9.0.0 — AnimBlueprint creation
#include "Animation/AnimBlueprint.h"
#include "Animation/AnimInstance.h"
#include "Animation/Skeleton.h"
#include "Factories/AnimBlueprintFactory.h"

// v9.2.0 — AnimGraph state machine authoring
#include "AnimGraphNode_StateMachine.h"
#include "AnimStateNode.h"
#include "AnimStateTransitionNode.h"
#include "AnimGraphNode_SequencePlayer.h"
#include "AnimationStateMachineGraph.h"
#include "Animation/AnimSequence.h"

// v9.3.0 — Niagara
// Note: UNiagaraSystemFactoryNew is NOT NIAGARAEDITOR_API-exported, so we
// resolve its UClass at runtime via FindObject (see CreateNiagaraSystemOnGameThread).
#include "NiagaraSystem.h"

// v9.4.0 — UMG + save_all
#include "WidgetBlueprint.h"
#include "WidgetBlueprintFactory.h"
#include "Blueprint/UserWidget.h"
#include "FileHelpers.h"   // FEditorFileUtils::SaveDirtyPackages

// v9.6.0 — Headless CI commandlet shutdown flag
#include "BlueprintMCPRunCommandlet.h"

// v9.9.0 — FTSTicker for asynchronous key-hold / move-player scheduling
#include "Containers/Ticker.h"

// v9.1.0 — asset / class discovery
#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "AssetRegistry/AssetData.h"
#include "UObject/UObjectIterator.h"   // already pulled in, but explicit

// v7.1 — set_component_property (FProperty reflection for component template defaults)
#include "UObject/UnrealType.h"        // FObjectProperty / FStructProperty / FClassProperty

// v7.2 — switch / sequence / make_array / select K2Nodes
#include "K2Node_Switch.h"
#include "K2Node_SwitchInteger.h"
#include "K2Node_SwitchString.h"
#include "K2Node_SwitchName.h"
#include "K2Node_SwitchEnum.h"
#include "K2Node_ExecutionSequence.h"
#include "K2Node_MakeArray.h"
#include "K2Node_Select.h"

// v7.3 — make_struct / break_struct
#include "K2Node_MakeStruct.h"
#include "K2Node_BreakStruct.h"

// v7.6 — event dispatchers (multicast delegates)
#include "K2Node_FunctionEntry.h"
#include "K2Node_CallDelegate.h"
#include "K2Node_AddDelegate.h"
#include "K2Node_RemoveDelegate.h"
#include "K2Node_BaseMCDelegate.h"   // v8.1.0: ghost dispatcher detection scans this base type

DEFINE_LOG_CATEGORY_STATIC(LogBlueprintMCP_TCP, Log, All);

// ============================================================
// v8.1 — Log capture (FBlueprintMCPLogCapture impl + global)
// Declared in TCPServer.h; installed in BlueprintMCPModule.cpp.
// ============================================================

FBlueprintMCPLogCapture* GBlueprintMCPLogCapture = nullptr;

void FBlueprintMCPLogCapture::Serialize(const TCHAR* V, ELogVerbosity::Type Verbosity, const FName& Category)
{
    if (V == nullptr || *V == TEXT('\0')) return;
    // Skip our own log spam (would loop on GLog->Add output during our own UE_LOG calls).
    if (Category == TEXT("LogBlueprintMCP") || Category == TEXT("LogBlueprintMCP_TCP"))
    {
        // Still capture them — they're useful — just don't recurse via Serialize.
        // FOutputDevice already protects against re-entry, but defensive.
    }

    // Compose: [LogCategory][Verbosity] message
    const TCHAR* VerbStr = ::ToString(Verbosity);   // "Warning", "Error", "Log", "Verbose", ...
    FString Formatted = FString::Printf(TEXT("[%s][%s] %s"),
        *Category.ToString(),
        (VerbStr != nullptr ? VerbStr : TEXT("Unknown")),
        V);

    FScopeLock Lock(&Mutex);
    if (Lines.Num() >= kMaxBufferedLines)
    {
        // Drop oldest. RemoveAt(0) is O(N) but kMaxBufferedLines is small.
        Lines.RemoveAt(0);
    }
    Lines.Add(MoveTemp(Formatted));
}

TArray<FString> FBlueprintMCPLogCapture::Snapshot(int32 MaxLines) const
{
    FScopeLock Lock(&Mutex);
    if (MaxLines <= 0 || MaxLines >= Lines.Num())
    {
        return Lines;   // full copy
    }
    TArray<FString> Tail;
    Tail.Reserve(MaxLines);
    const int32 Start = Lines.Num() - MaxLines;
    for (int32 i = Start; i < Lines.Num(); ++i)
    {
        Tail.Add(Lines[i]);
    }
    return Tail;
}

void FBlueprintMCPLogCapture::Clear()
{
    FScopeLock Lock(&Mutex);
    Lines.Empty();
}

namespace
{
    // Forward declarations: helpers defined later in this anon namespace that
    // are referenced from "OnGameThread" functions defined earlier in source.
    // (C++ requires either forward decl or definition-before-use.)
    FString FormatStructDefault(UScriptStruct* StructType, const FString& UserInput);
    bool IsSupportedStructForDefault(UScriptStruct* StructType);
    // v7.4: ResolveVariablePinType uses ResolveCastTargetClass for object/class ref types,
    // but the latter is defined further down. Forward-declare here.
    UClass* ResolveCastTargetClass(const FString& Name);
    // v7.7 fix: JsonGraphNotFound (below) calls JsonError, which is defined at L202.
    FString JsonError(const FString& Command, const FString& Error, const FString& Detail);

    /**
     * v7.7: Resolve which graph a graph-writing tool should target.
     * Empty GraphName → EventGraph (Blueprint->UbergraphPages[0]).
     * Otherwise look up by FName in FunctionGraphs / MacroGraphs / UbergraphPages.
     * Returns nullptr if not found.
     */
    UEdGraph* ResolveTargetGraph(UBlueprint* Blueprint, const FString& GraphName)
    {
        if (Blueprint == nullptr) return nullptr;
        if (GraphName.IsEmpty() || GraphName.Equals(TEXT("EventGraph"), ESearchCase::IgnoreCase))
        {
            return (Blueprint->UbergraphPages.Num() > 0) ? Blueprint->UbergraphPages[0] : nullptr;
        }
        const FName Name(*GraphName);
        for (UEdGraph* G : Blueprint->FunctionGraphs)
        {
            if (G != nullptr && G->GetFName() == Name) return G;
        }
        for (UEdGraph* G : Blueprint->MacroGraphs)
        {
            if (G != nullptr && G->GetFName() == Name) return G;
        }
        for (UEdGraph* G : Blueprint->UbergraphPages)
        {
            if (G != nullptr && G->GetFName() == Name) return G;
        }
        return nullptr;
    }

    /** v7.7 helper: build the standard "graph_not_found" JSON error for a tool. */
    FString JsonGraphNotFound(const TCHAR* CmdName, const FString& GraphName)
    {
        return JsonError(CmdName, TEXT("graph_not_found"),
            GraphName.IsEmpty()
                ? FString(TEXT("EventGraph (Blueprint has no UbergraphPages)"))
                : GraphName);
    }

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
     * v6.0.2: added Verbose log so future P2-style reports can be diagnosed from UE Output Log.
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
        const FKey Result(*Resolved);
        UE_LOG(LogBlueprintMCP_TCP, Verbose,
            TEXT("ResolveFKeyWithAliases: input='%s' lower='%s' mapped='%s' resolved='%s' valid=%s"),
            *Name, *Lower,
            Mapped ? **Mapped : TEXT("<no-alias>"),
            *Resolved, Result.IsValid() ? TEXT("true") : TEXT("false"));
        return Result;
    }

    /** Build an FEdGraphPinType for a user-friendly variable type key. v1+v5+v7.4 whitelist. */
    bool ResolveVariablePinType(const FString& TypeKey, FEdGraphPinType& OutType)
    {
        OutType = FEdGraphPinType();

        // v5: array types — "int[]" / "float[]" / "string[]" / "bool[]" / "name[]" / "object:Actor[]"
        FString BaseType = TypeKey;
        if (BaseType.EndsWith(TEXT("[]")))
        {
            BaseType = BaseType.LeftChop(2);  // strip "[]"
            OutType.ContainerType = EPinContainerType::Array;
        }

        // v7.4: object reference types — "object:Actor", "object:/Script/Engine.Pawn", "object:BP_X"
        if (BaseType.StartsWith(TEXT("object:"), ESearchCase::IgnoreCase))
        {
            const FString ClassName = BaseType.Mid(7);  // strip "object:"
            UClass* TargetClass = ResolveCastTargetClass(ClassName);
            if (TargetClass == nullptr) return false;
            OutType.PinCategory = UEdGraphSchema_K2::PC_Object;
            OutType.PinSubCategoryObject = TargetClass;
            return true;
        }
        // v7.4: class reference types — "class:Actor", "class:/Script/Engine.Pawn"
        if (BaseType.StartsWith(TEXT("class:"), ESearchCase::IgnoreCase))
        {
            const FString ClassName = BaseType.Mid(6);  // strip "class:"
            UClass* TargetClass = ResolveCastTargetClass(ClassName);
            if (TargetClass == nullptr) return false;
            OutType.PinCategory = UEdGraphSchema_K2::PC_Class;
            OutType.PinSubCategoryObject = TargetClass;
            return true;
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

    /** Build the JSON array string describing a node's pins.
     *  v7.1.1 (BUG-4 fix): skip Pin->bHidden so internal pins (e.g. K2Node_Switch's
     *  NotEqual_IntInt function-ref pin, K2Node_CallFunction's self when implicit)
     *  don't leak into the public pin list. */
    FString BuildPinsJsonArray(const UEdGraphNode* Node)
    {
        TArray<FString> PinJsonItems;
        for (const UEdGraphPin* Pin : Node->Pins)
        {
            if (Pin->bHidden) continue;   // BUG-4 fix
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
     * Add a node to a Blueprint graph. MUST run on the game thread.
     * v7.7: defaults to EventGraph; pass GraphName="MyFunc" to target a function graph.
     * Returns a complete JSON response line (with trailing \n).
     */
    FString AddNodeOnGameThread(
        const FString& BlueprintPath,
        const FString& NodeType,
        const FString& AnchorName,
        int32 PosX,
        int32 PosY,
        const FString& GraphName = FString())   // v7.7: optional target graph
    {
        check(IsInGameThread());

        // 1. Load Blueprint
        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr)
        {
            return JsonError(TEXT("add_node"), TEXT("blueprint_not_found"), BlueprintPath);
        }

        // 2. Resolve target graph (EventGraph or function/macro graph)
        UEdGraph* EventGraph = ResolveTargetGraph(Blueprint, GraphName);
        if (EventGraph == nullptr)
        {
            return JsonGraphNotFound(TEXT("add_node"), GraphName);
        }

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
     *   3. v6.0.4 GUID-prefix fallback — anchors of the form "node_<hex prefix>"
     *      (as emitted by DeriveAnchorForNode for un-commented nodes) match by
     *      NodeGuid prefix. Prefix must be ≥ 4 chars.
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

        // 3. v6.0.4 fix (P7): GUID-prefix fallback. DeriveAnchorForNode emits
        //    "node_<8-char-lowercase-guid>" for nodes without NodeComment (e.g. nodes
        //    the user added manually in the editor, not via add_*). Before this fix,
        //    those anchors were read-only — get_blueprint emitted them but tools
        //    couldn't resolve them back. Now any prefix ≥4 chars matches.
        if (AnchorName.StartsWith(TEXT("node_"), ESearchCase::CaseSensitive))
        {
            const FString GuidPrefix = AnchorName.Mid(5);
            if (GuidPrefix.Len() >= 4)
            {
                const FString GuidPrefixLower = GuidPrefix.ToLower();
                for (UEdGraphNode* Node : Graph->Nodes)
                {
                    if (Node == nullptr) continue;
                    const FString NodeGuidStr = Node->NodeGuid.ToString(EGuidFormats::DigitsLower);
                    if (NodeGuidStr.StartsWith(GuidPrefixLower))
                    {
                        return Node;
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
        const FString& Value,
        const FString& GraphName = FString())   // v7.7
    {
        check(IsInGameThread());

        // 1. Load Blueprint
        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr)
        {
            return JsonError(TEXT("set_pin_default"), TEXT("blueprint_not_found"), BlueprintPath);
        }

        // 2. Resolve target graph (v7.7: EventGraph by default, named function/macro graph if requested)
        UEdGraph* EventGraph = ResolveTargetGraph(Blueprint, GraphName);
        if (EventGraph == nullptr)
        {
            return JsonGraphNotFound(TEXT("set_pin_default"), GraphName);
        }

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
        // v1 primitive types OR v4 struct types OR v6.0.2 class/object asset refs
        const FName Category = TargetPin->PinType.PinCategory;
        UScriptStruct* StructType = nullptr;
        FString ValueToSet = Value;
        UObject* ObjectToSet = nullptr;   // for class / object pins (v6.0.2)
        bool bUseObjectSetter = false;

        if (Category == UEdGraphSchema_K2::PC_Struct)
        {
            StructType = Cast<UScriptStruct>(TargetPin->PinType.PinSubCategoryObject.Get());
            if (!IsSupportedStructForDefault(StructType))
            {
                return JsonError(TEXT("set_pin_default"), TEXT("unsupported_struct_type"),
                    StructType ? StructType->GetName() : TEXT("unknown"));
            }
            ValueToSet = FormatStructDefault(StructType, Value);
        }
        else if (Category == UEdGraphSchema_K2::PC_Class || Category == UEdGraphSchema_K2::PC_SoftClass)
        {
            // v6.0.2 P3 fix: class pin — value is a class name (e.g. "EnhancedInputLocalPlayerSubsystem"
            // or a fully-qualified "/Script/EnhancedInput.EnhancedInputLocalPlayerSubsystem").
            UClass* Found = FindFirstObject<UClass>(*Value, EFindFirstObjectOptions::NativeFirst);
            if (!Found)
            {
                // Try as a full class path
                Found = LoadObject<UClass>(nullptr, *Value);
            }
            if (!Found)
            {
                return JsonError(TEXT("set_pin_default"), TEXT("class_not_found"), Value);
            }
            ObjectToSet = Found;
            bUseObjectSetter = true;
        }
        else if (Category == UEdGraphSchema_K2::PC_Object || Category == UEdGraphSchema_K2::PC_SoftObject ||
                 Category == UEdGraphSchema_K2::PC_Interface)
        {
            // v6.0.2 P3 fix: object pin — value is an asset path like "/Game/Input/IMC_Default"
            UObject* Asset = LoadObject<UObject>(nullptr, *Value);
            if (!Asset)
            {
                return JsonError(TEXT("set_pin_default"), TEXT("asset_not_found"), Value);
            }
            ObjectToSet = Asset;
            bUseObjectSetter = true;
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
        if (bUseObjectSetter)
        {
            Schema->TrySetDefaultObject(*TargetPin, ObjectToSet, /*bMarkAsModified*/ true);
        }
        else
        {
            Schema->TrySetDefaultValue(*TargetPin, ValueToSet, /*bMarkAsModified*/ true);
        }

        // 8. Mark BP modified + save
        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);
        if (!bSaved)
        {
            UE_LOG(LogBlueprintMCP_TCP, Warning,
                TEXT("set_pin_default: value set but save failed (%s)"), *BlueprintPath);
        }

        // 9. Build response — report what UE actually stored (may differ after coercion)
        // For object/class pins, value is the DefaultObject's path (since DefaultValue is empty)
        FString StoredValue;
        if (bUseObjectSetter && TargetPin->DefaultObject)
        {
            StoredValue = TargetPin->DefaultObject->GetPathName();
        }
        else
        {
            StoredValue = TargetPin->DefaultValue;
        }
        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"set_pin_default\",\"anchor_name\":%s,\"pin_name\":%s,\"value\":%s,\"pin_type\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(AnchorName),
            *EscapeJsonString(PinName),
            *EscapeJsonString(StoredValue),
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

    // ===== v7.1 — set_component_property (FProperty reflection) =====

    /**
     * Walk a dot-separated property path on a UStruct/UObject container.
     * Returns the leaf FProperty + sets OutValuePtr to its storage location.
     *
     * Examples:
     *   "StaticMesh"                        → top-level FObjectProperty
     *   "BoxExtent"                         → top-level FStructProperty (FVector)
     *   "BodyInstance.CollisionProfileName" → nested FNameProperty inside FBodyInstance
     *
     * Returns nullptr if any token doesn't resolve or a mid-path token isn't a struct.
     */
    FProperty* WalkPropertyPath(
        UStruct* RootStruct,
        void* RootContainer,
        const FString& PropertyPath,
        void*& OutValuePtr)
    {
        OutValuePtr = nullptr;
        TArray<FString> Tokens;
        PropertyPath.ParseIntoArray(Tokens, TEXT("."), /*InCullEmpty*/ true);
        if (Tokens.Num() == 0 || RootStruct == nullptr || RootContainer == nullptr)
        {
            return nullptr;
        }

        UStruct* CurrentStruct = RootStruct;
        void* CurrentContainer = RootContainer;

        for (int32 i = 0; i < Tokens.Num(); ++i)
        {
            FProperty* Prop = FindFProperty<FProperty>(CurrentStruct, *Tokens[i]);
            if (Prop == nullptr) return nullptr;

            if (i == Tokens.Num() - 1)
            {
                OutValuePtr = Prop->ContainerPtrToValuePtr<void>(CurrentContainer);
                return Prop;
            }

            // Intermediate token must be a struct field — descend into it
            FStructProperty* StructProp = CastField<FStructProperty>(Prop);
            if (StructProp == nullptr) return nullptr;
            CurrentContainer = Prop->ContainerPtrToValuePtr<void>(CurrentContainer);
            CurrentStruct = StructProp->Struct;
        }
        return nullptr;
    }

    /**
     * Set a property on a component's template instance via reflection.
     * Dispatches by FProperty subclass:
     *   - FObjectProperty (StaticMesh, Material, …) → LoadObject + class check + SetObjectPropertyValue
     *   - FClassProperty  (TSubclassOf<X>)          → LoadObject<UClass> + meta-class check
     *   - everything else (struct/primitive/FName/enum/bool/FString) → ImportText_Direct
     *
     * For known structs (Vector/Rotator/Color), value is pre-normalized through
     * FormatStructDefault so users can pass shorthand "200,200,200".
     */
    FString SetComponentPropertyOnGameThread(
        const FString& BlueprintPath,
        const FString& ComponentName,
        const FString& PropertyPath,
        const FString& Value)
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("set_component_property");

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr)
        {
            return JsonError(CmdName, TEXT("blueprint_not_found"), BlueprintPath);
        }
        if (Blueprint->ParentClass == nullptr || !Blueprint->ParentClass->IsChildOf(AActor::StaticClass()))
        {
            return JsonError(CmdName, TEXT("parent_not_actor"),
                TEXT("Component properties require an Actor-derived Blueprint"));
        }

        USimpleConstructionScript* SCS = Blueprint->SimpleConstructionScript;
        if (SCS == nullptr)
        {
            return JsonError(CmdName, TEXT("no_scs"), BlueprintPath);
        }

        const FName ComponentFName(*ComponentName);
        USCS_Node* SCSNode = SCS->FindSCSNode(ComponentFName);
        if (SCSNode == nullptr)
        {
            return JsonError(CmdName, TEXT("component_not_found"), ComponentName);
        }

        UActorComponent* Template = SCSNode->ComponentTemplate;
        if (Template == nullptr)
        {
            return JsonError(CmdName, TEXT("no_component_template"), ComponentName);
        }

        void* ValuePtr = nullptr;
        FProperty* LeafProp = WalkPropertyPath(Template->GetClass(), Template, PropertyPath, ValuePtr);
        if (LeafProp == nullptr || ValuePtr == nullptr)
        {
            return JsonError(CmdName, TEXT("property_not_found"),
                FString::Printf(TEXT("Component '%s' (class %s) has no property '%s'"),
                    *ComponentName, *Template->GetClass()->GetName(), *PropertyPath));
        }

        Template->PreEditChange(LeafProp);

        FString ResolvedValueStr;
        FString ErrorDetail;
        bool bSuccess = false;

        // --- Object / asset reference (FObjectProperty) ---
        if (FObjectProperty* ObjProp = CastField<FObjectProperty>(LeafProp))
        {
            UObject* Asset = nullptr;
            const bool bClear = Value.IsEmpty()
                || Value.Equals(TEXT("None"), ESearchCase::IgnoreCase)
                || Value.Equals(TEXT("null"), ESearchCase::IgnoreCase);
            if (!bClear)
            {
                Asset = LoadObject<UObject>(nullptr, *Value);
                if (Asset == nullptr)
                {
                    ErrorDetail = FString::Printf(TEXT("Asset not found: %s"), *Value);
                }
                else if (!Asset->IsA(ObjProp->PropertyClass))
                {
                    ErrorDetail = FString::Printf(
                        TEXT("Asset '%s' is %s but property expects %s"),
                        *Value, *Asset->GetClass()->GetName(),
                        *ObjProp->PropertyClass->GetName());
                    Asset = nullptr;
                }
            }
            if (ErrorDetail.IsEmpty())
            {
                ObjProp->SetObjectPropertyValue(ValuePtr, Asset);
                ResolvedValueStr = (Asset != nullptr) ? Asset->GetPathName() : TEXT("None");
                bSuccess = true;
            }
        }
        // --- Class reference (FClassProperty / TSubclassOf<X>) ---
        else if (FClassProperty* ClassProp = CastField<FClassProperty>(LeafProp))
        {
            UClass* Class = nullptr;
            const bool bClear = Value.IsEmpty()
                || Value.Equals(TEXT("None"), ESearchCase::IgnoreCase)
                || Value.Equals(TEXT("null"), ESearchCase::IgnoreCase);
            if (!bClear)
            {
                Class = LoadObject<UClass>(nullptr, *Value);
                if (Class == nullptr)
                {
                    ErrorDetail = FString::Printf(TEXT("Class not found: %s"), *Value);
                }
                else if (ClassProp->MetaClass != nullptr && !Class->IsChildOf(ClassProp->MetaClass))
                {
                    ErrorDetail = FString::Printf(
                        TEXT("Class '%s' is not a subclass of %s"),
                        *Value, *ClassProp->MetaClass->GetName());
                    Class = nullptr;
                }
            }
            if (ErrorDetail.IsEmpty())
            {
                ClassProp->SetObjectPropertyValue(ValuePtr, Class);
                ResolvedValueStr = (Class != nullptr) ? Class->GetPathName() : TEXT("None");
                bSuccess = true;
            }
        }
        // --- Struct / primitive / FName / enum / bool / FString — use ImportText_Direct ---
        // For known structs (Vector/Rotator/Color), normalize shorthand "1,2,3" → "(X=1,Y=2,Z=3)" first.
        else
        {
            FString NormalizedValue = Value;
            if (FStructProperty* StructProp = CastField<FStructProperty>(LeafProp))
            {
                if (IsSupportedStructForDefault(StructProp->Struct))
                {
                    NormalizedValue = FormatStructDefault(StructProp->Struct, Value);
                }
            }

            const TCHAR* Buffer = *NormalizedValue;
            const TCHAR* Result = LeafProp->ImportText_Direct(
                Buffer, ValuePtr, /*OwnerObject*/ Template, PPF_None);

            if (Result == nullptr)
            {
                ErrorDetail = FString::Printf(
                    TEXT("Failed to parse value '%s' for property '%s' (type %s)"),
                    *NormalizedValue, *PropertyPath, *LeafProp->GetClass()->GetName());
            }
            else
            {
                ResolvedValueStr = NormalizedValue;
                bSuccess = true;
            }
        }

        if (!bSuccess)
        {
            return JsonError(CmdName, TEXT("set_failed"), ErrorDetail);
        }

        FPropertyChangedEvent ChangeEvent(LeafProp, EPropertyChangeType::ValueSet);
        Template->PostEditChangeProperty(ChangeEvent);

        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"set_component_property\",\"blueprint\":%s,\"component\":%s,\"property\":%s,\"resolved_value\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(BlueprintPath),
            *EscapeJsonString(ComponentName),
            *EscapeJsonString(PropertyPath),
            *EscapeJsonString(ResolvedValueStr),
            bSaved ? TEXT("true") : TEXT("false"));
    }

    // ===== Spike B8 — add_custom_event (v7.5: with optional parameters) =====

    FString AddCustomEventOnGameThread(
        const FString& BlueprintPath,
        const FString& EventName,
        const FString& AnchorName,
        int32 PosX, int32 PosY,
        const TArray<FString>& ParamNames,    // v7.5: parallel arrays of param specs
        const TArray<FString>& ParamTypes,
        const FString& GraphName = FString())   // v7.7.1
    {
        check(IsInGameThread());

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr)
        {
            return JsonError(TEXT("add_custom_event"), TEXT("blueprint_not_found"), BlueprintPath);
        }
        UEdGraph* EventGraph = ResolveTargetGraph(Blueprint, GraphName);
        if (EventGraph == nullptr)
        {
            return JsonGraphNotFound(TEXT("add_custom_event"), GraphName);
        }

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

        // v7.5: pre-validate all param types BEFORE creating the node so we can fail cleanly
        if (ParamNames.Num() != ParamTypes.Num())
        {
            return JsonError(TEXT("add_custom_event"), TEXT("param_arity_mismatch"),
                FString::Printf(TEXT("params: %d names but %d types"), ParamNames.Num(), ParamTypes.Num()));
        }
        TArray<FEdGraphPinType> ResolvedParamTypes;
        ResolvedParamTypes.Reserve(ParamNames.Num());
        for (int32 i = 0; i < ParamNames.Num(); ++i)
        {
            FEdGraphPinType PinType;
            if (!ResolveVariablePinType(ParamTypes[i], PinType))
            {
                return JsonError(TEXT("add_custom_event"), TEXT("unknown_param_type"),
                    FString::Printf(TEXT("param '%s' has unknown type '%s'"),
                        *ParamNames[i], *ParamTypes[i]));
            }
            ResolvedParamTypes.Add(PinType);
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

        // v7.5: add user-defined parameter pins. For CustomEvent, params are OUTPUT pins
        // (the event emits param values into the graph). CreateUserDefinedPin both adds to
        // UserDefinedPins TArray AND creates the actual UEdGraphPin in one call.
        for (int32 i = 0; i < ParamNames.Num(); ++i)
        {
            const FName ParamFName(*ParamNames[i]);
            NewNode->CreateUserDefinedPin(ParamFName, ResolvedParamTypes[i], EGPD_Output, /*bUseUniqueName*/ false);
        }

        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);

        const FString PinsJson = BuildPinsJsonArray(NewNode);
        const FString GuidStr = NewNode->NodeGuid.ToString(EGuidFormats::DigitsWithHyphens);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"add_custom_event\",\"anchor_name\":%s,\"event_name\":%s,\"node_guid\":%s,\"param_count\":%d,\"pins\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(AnchorName),
            *EscapeJsonString(EventName),
            *EscapeJsonString(GuidStr),
            ParamNames.Num(),
            *PinsJson,
            bSaved ? TEXT("true") : TEXT("false"));
    }

    // ===== Spike B9 — add_variable =====

    FString AddVariableOnGameThread(
        const FString& BlueprintPath,
        const FString& VarName,
        const FString& VarTypeKey,
        const FString& DefaultValue,
        bool bInstanceEditable = false)   // v9.8.0 — closes feature-request #5
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

        // v9.8.0 — apply instance_editable flag if requested. UE stores this as
        // the NEGATIVE flag CPF_DisableEditOnInstance — clear bit = editable.
        if (bInstanceEditable)
        {
            uint64* PropertyFlags = FBlueprintEditorUtils::GetBlueprintVariablePropertyFlags(Blueprint, VarFName);
            if (PropertyFlags != nullptr)
                *PropertyFlags &= ~CPF_DisableEditOnInstance;
        }

        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"add_variable\",\"variable_name\":%s,\"variable_type\":%s,\"instance_editable\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(VarName),
            *EscapeJsonString(VarTypeKey),
            bInstanceEditable ? TEXT("true") : TEXT("false"),
            bSaved ? TEXT("true") : TEXT("false"));
    }

    // ===== v9.8.0 — Blueprint / variable lifecycle =====
    //
    // Closes feature-request gaps #1, #5, #8 from the 2026-05-21 review.

    /**
     * Set instance_editable / blueprint_read_only / expose_on_spawn on an
     * existing BP variable. Each tri-state — None / unset = leave unchanged.
     */
    FString SetVariableFlagsOnGameThread(
        const FString& BlueprintPath,
        const FString& VarName,
        bool bHasInstanceEditable, bool bInstanceEditable,
        bool bHasReadOnly,         bool bReadOnly,
        bool bHasExposeOnSpawn,    bool bExposeOnSpawn)
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("set_variable_flags");

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr)
            return JsonError(CmdName, TEXT("blueprint_not_found"), BlueprintPath);

        const FName VarFName(*VarName);
        if (FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, VarFName) == INDEX_NONE)
            return JsonError(CmdName, TEXT("variable_not_found"), VarName);

        uint64* PropertyFlags = FBlueprintEditorUtils::GetBlueprintVariablePropertyFlags(Blueprint, VarFName);
        if (PropertyFlags == nullptr)
            return JsonError(CmdName, TEXT("no_property_flags"), VarName);

        if (bHasInstanceEditable)
        {
            if (bInstanceEditable) *PropertyFlags &= ~CPF_DisableEditOnInstance;
            else                   *PropertyFlags |=  CPF_DisableEditOnInstance;
        }
        if (bHasReadOnly)
        {
            if (bReadOnly) *PropertyFlags |=  CPF_BlueprintReadOnly;
            else           *PropertyFlags &= ~CPF_BlueprintReadOnly;
        }
        if (bHasExposeOnSpawn)
        {
            FBlueprintEditorUtils::SetBlueprintVariableMetaData(
                Blueprint, VarFName, /*InLocalVarScope*/ nullptr,
                FName(TEXT("ExposeOnSpawn")),
                bExposeOnSpawn ? FString(TEXT("true")) : FString(TEXT("false")));
        }

        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        // Recompile so flag changes propagate to the GeneratedClass FProperty
        FKismetEditorUtilities::CompileBlueprint(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"set_variable_flags\",\"variable_name\":%s,")
            TEXT("\"instance_editable\":%s,\"blueprint_read_only\":%s,\"expose_on_spawn\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(VarName),
            (*PropertyFlags & CPF_DisableEditOnInstance) ? TEXT("false") : TEXT("true"),
            (*PropertyFlags & CPF_BlueprintReadOnly) ? TEXT("true") : TEXT("false"),
            bHasExposeOnSpawn ? (bExposeOnSpawn ? TEXT("true") : TEXT("false")) : TEXT("null"),
            bSaved ? TEXT("true") : TEXT("false"));
    }

    FString DeleteVariableOnGameThread(const FString& BlueprintPath, const FString& VarName)
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("delete_variable");

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr)
            return JsonError(CmdName, TEXT("blueprint_not_found"), BlueprintPath);

        const FName VarFName(*VarName);
        if (FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, VarFName) == INDEX_NONE)
            return JsonError(CmdName, TEXT("variable_not_found"), VarName);

        FBlueprintEditorUtils::RemoveMemberVariable(Blueprint, VarFName);
        FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
        FKismetEditorUtilities::CompileBlueprint(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"delete_variable\",\"variable_name\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(VarName),
            bSaved ? TEXT("true") : TEXT("false"));
    }

    FString DeleteBlueprintOnGameThread(const FString& BlueprintPath)
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("delete_blueprint");

        if (!UEditorAssetLibrary::DoesAssetExist(BlueprintPath))
            return JsonError(CmdName, TEXT("asset_not_found"), BlueprintPath);

        // Optional sanity: confirm it's actually a Blueprint asset (defensive
        // against accidental deletion of e.g. textures via this tool).
        UObject* Asset = UEditorAssetLibrary::LoadAsset(BlueprintPath);
        if (Asset != nullptr && !Asset->IsA(UBlueprint::StaticClass()))
        {
            return JsonError(CmdName, TEXT("not_a_blueprint"),
                FString::Printf(TEXT("Asset at %s is %s, not a UBlueprint. Use delete_asset for non-BP assets (not yet implemented)."),
                    *BlueprintPath, *Asset->GetClass()->GetName()));
        }

        const bool bOk = UEditorAssetLibrary::DeleteAsset(BlueprintPath);
        return FString::Printf(
            TEXT("{\"ok\":%s,\"command\":\"delete_blueprint\",\"blueprint_path\":%s,\"deleted\":%s}\n"),
            bOk ? TEXT("true") : TEXT("false"),
            *EscapeJsonString(BlueprintPath),
            bOk ? TEXT("true") : TEXT("false"));
    }

    // ===== Spike B10 — add_variable_get / add_variable_set =====

    FString AddVariableRefOnGameThread(
        const FString& BlueprintPath,
        const FString& VariableName,
        const FString& AnchorName,
        int32 PosX, int32 PosY,
        bool bIsSet,
        const FString& GraphName = FString())   // v7.7.1
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

        // BUG-3 fix (a): tag the auto-created K2Node_FunctionEntry node with a well-known
        // anchor ("entry") so connect_pins(graph_name="MyFunc", from_pin="entry.then", ...)
        // can address it via FindNodeByAnchor.
        for (UEdGraphNode* N : NewFuncGraph->Nodes)
        {
            if (Cast<UK2Node_FunctionEntry>(N) != nullptr)
            {
                N->NodeComment = TEXT("entry");
                N->bCommentBubbleVisible = true;
                break;
            }
        }

        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, false);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"add_function\",\"function_name\":%s,\"entry_anchor\":\"entry\",\"saved\":%s}\n"),
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
        int32 PosX, int32 PosY,
        const FString& TargetPinRef = FString(),  // v6: optional, auto-wire self
        const FString& GraphName    = FString())  // v7.7.1
    {
        check(IsInGameThread());

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (!Blueprint) return JsonError(TEXT("call_blueprint_function"), TEXT("blueprint_not_found"), BlueprintPath);
        UEdGraph* EventGraph = ResolveTargetGraph(Blueprint, GraphName);
        if (EventGraph == nullptr) return JsonGraphNotFound(TEXT("call_blueprint_function"), GraphName);

        if (FindNodeByAnchor(EventGraph, AnchorName))
            return JsonError(TEXT("call_blueprint_function"), TEXT("anchor_name_exists"), AnchorName);

        UClass* TargetClass = ResolveCallTargetClass(TargetClassStr);
        if (!TargetClass)
            return JsonError(TEXT("call_blueprint_function"), TEXT("target_class_not_found"), TargetClassStr);

        UFunction* TargetFunc = TargetClass->FindFunctionByName(FName(*FunctionName));
        bool bAutoCompiled = false;

        // v7.1.3: function not found may just mean the target BP wasn't compiled since the
        // function was added (common when LLM does `add_function` then immediately
        // `call_blueprint_function`). If TargetClass is BP-generated, compile its owning BP
        // and retry the lookup once. Native classes skip this fallback.
        if (!TargetFunc)
        {
            UBlueprint* OwningBP = Cast<UBlueprint>(TargetClass->ClassGeneratedBy);
            if (OwningBP != nullptr)
            {
                UE_LOG(LogBlueprintMCP_TCP, Log,
                    TEXT("call_blueprint_function: '%s' not found on %s — auto-compiling owning BP %s and retrying"),
                    *FunctionName, *TargetClass->GetName(), *OwningBP->GetName());
                FKismetEditorUtilities::CompileBlueprint(OwningBP, EBlueprintCompileOptions::None);
                bAutoCompiled = true;
                // GeneratedClass may be reassigned after compile (rare); refresh from BP
                if (OwningBP->GeneratedClass != nullptr)
                {
                    TargetClass = OwningBP->GeneratedClass;
                    TargetFunc = TargetClass->FindFunctionByName(FName(*FunctionName));
                }
            }
            if (!TargetFunc)
            {
                return JsonError(TEXT("call_blueprint_function"), TEXT("function_not_found"),
                    FString::Printf(TEXT("%s on %s (auto_compile_attempted=%s)"),
                        *FunctionName, *TargetClass->GetName(),
                        bAutoCompiled ? TEXT("yes") : TEXT("no — TargetClass is not BP-generated")));
            }
        }

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

        // v6: optionally auto-wire self pin from a target pin
        bool bSelfWired = false;
        FString TargetPinError;
        if (!TargetPinRef.IsEmpty())
        {
            FString TargetErr;
            UEdGraphPin* SourcePin = ResolvePinRef(EventGraph, TargetPinRef, TEXT("call_blueprint_function"), TargetErr);
            UEdGraphPin* SelfPin = NewNode->FindPin(UEdGraphSchema_K2::PN_Self);
            if (!SelfPin)
            {
                // Fallback: try literal "self" pin name
                SelfPin = NewNode->FindPin(FName("self"));
            }
            if (SourcePin && SelfPin)
            {
                const UEdGraphSchema_K2* Schema = Cast<UEdGraphSchema_K2>(EventGraph->GetSchema());
                if (Schema && Schema->TryCreateConnection(SourcePin, SelfPin))
                {
                    bSelfWired = true;
                }
                else
                {
                    TargetPinError = FString::Printf(TEXT("Could not wire %s -> %s.self"),
                        *TargetPinRef, *AnchorName);
                }
            }
            else if (!SourcePin)
            {
                TargetPinError = TargetErr;  // already JSON; we'll embed in detail
            }
            else
            {
                TargetPinError = TEXT("call node has no self pin (static function?)");
            }
        }

        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, false);

        const FString PinsJson = BuildPinsJsonArray(NewNode);
        const FString GuidStr = NewNode->NodeGuid.ToString(EGuidFormats::DigitsWithHyphens);

        // Include target-pin wiring outcome in response if user asked for it
        FString WiringJsonFragment;
        if (!TargetPinRef.IsEmpty())
        {
            if (bSelfWired)
            {
                WiringJsonFragment = FString::Printf(
                    TEXT(",\"self_wired\":true,\"self_source\":%s"),
                    *EscapeJsonString(TargetPinRef));
            }
            else
            {
                WiringJsonFragment = FString::Printf(
                    TEXT(",\"self_wired\":false,\"self_wire_error\":%s"),
                    *EscapeJsonString(TargetPinError));
            }
        }

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"call_blueprint_function\",\"anchor_name\":%s,\"node_guid\":%s,\"target_class\":%s,\"function\":%s,\"auto_compiled\":%s,\"pins\":%s%s,\"saved\":%s}\n"),
            *EscapeJsonString(AnchorName), *EscapeJsonString(GuidStr),
            *EscapeJsonString(TargetClass->GetName()), *EscapeJsonString(FunctionName),
            bAutoCompiled ? TEXT("true") : TEXT("false"),
            *PinsJson, *WiringJsonFragment,
            bSaved ? TEXT("true") : TEXT("false"));
    }

    // ===== v6 — wire_imc_subscribe (high-level macro) =====

    /**
     * Build the runtime IMC subscription chain in a BP's EventGraph:
     *   BeginPlay → AddMappingContext(MappingContext=IMC, Priority=N)
     *               .self ← GetSubsystem<UEnhancedInputLocalPlayerSubsystem>
     *                         .PlayerController ← GetPlayerController(0)
     *
     * After this runs in PIE, the IMC is actually active and Enhanced Input
     * events (added via add_enhanced_input_node) will fire.
     */
    FString WireImcSubscribeOnGameThread(
        const FString& BlueprintPath,
        const FString& IMCPath,
        int32 Priority,
        const FString& AnchorPrefix)
    {
        check(IsInGameThread());

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (!Blueprint) return JsonError(TEXT("wire_imc_subscribe"), TEXT("blueprint_not_found"), BlueprintPath);
        if (Blueprint->UbergraphPages.Num() == 0)
            return JsonError(TEXT("wire_imc_subscribe"), TEXT("no_event_graph"), BlueprintPath);
        UEdGraph* EventGraph = Blueprint->UbergraphPages[0];

        UInputMappingContext* IMC = LoadObject<UInputMappingContext>(nullptr, *IMCPath);
        if (!IMC) return JsonError(TEXT("wire_imc_subscribe"), TEXT("imc_not_found"), IMCPath);

        // Generate anchor names + check uniqueness. v6.0.2: add a 4th node (Cast) because
        // GetLocalPlayerSubSystemFromPlayerController returns base ULocalPlayerSubsystem
        // and DeterminesOutputType meta doesn't propagate through K2Node_CallFunction
        // reliably for BlueprintInternalUseOnly functions called from outside their K2Node.
        // An explicit Cast<EnhancedInputLocalPlayerSubsystem> sidesteps the issue cleanly.
        const FString PCAnchor   = AnchorPrefix + TEXT("_get_pc");
        const FString SubAnchor  = AnchorPrefix + TEXT("_get_sub");
        const FString CastAnchor = AnchorPrefix + TEXT("_cast");
        const FString AddAnchor  = AnchorPrefix + TEXT("_add_ctx");
        for (const FString& A : { PCAnchor, SubAnchor, CastAnchor, AddAnchor })
        {
            if (FindNodeByAnchor(EventGraph, A) != nullptr)
            {
                return JsonError(TEXT("wire_imc_subscribe"), TEXT("anchor_name_exists"), A);
            }
        }

        // Schema needed for pin-default setters (triggers PinDefaultValueChanged → type propagation)
        const UEdGraphSchema_K2* Schema = Cast<UEdGraphSchema_K2>(EventGraph->GetSchema());
        if (!Schema) return JsonError(TEXT("wire_imc_subscribe"), TEXT("schema_not_k2"), TEXT(""));

        // Find or spawn BeginPlay
        UEdGraphNode* BeginPlay = FindOrSpawnNodeByAnchor(EventGraph, TEXT("begin_play"));
        if (!BeginPlay) return JsonError(TEXT("wire_imc_subscribe"), TEXT("begin_play_unavailable"),
            TEXT("BP parent class may not expose ReceiveBeginPlay"));
        UEdGraphPin* BeginPlayThen = BeginPlay->FindPin(FName("then"));
        if (!BeginPlayThen) return JsonError(TEXT("wire_imc_subscribe"), TEXT("begin_play_no_then_pin"), TEXT(""));

        // Position chain to the right of BeginPlay
        const int32 BaseX = BeginPlay->NodePosX + 350;
        const int32 BaseY = BeginPlay->NodePosY + 250;

        // --- Node 1: GetPlayerController(0) ---
        UClass* GameplayStaticsClass = UGameplayStatics::StaticClass();
        UFunction* GetPCFunc = GameplayStaticsClass->FindFunctionByName(FName("GetPlayerController"));
        if (!GetPCFunc) return JsonError(TEXT("wire_imc_subscribe"), TEXT("getplayercontroller_missing"), TEXT(""));

        UK2Node_CallFunction* GetPCNode = NewObject<UK2Node_CallFunction>(EventGraph);
        GetPCNode->SetFlags(RF_Transactional);
        GetPCNode->FunctionReference.SetExternalMember(GetPCFunc->GetFName(), GameplayStaticsClass);
        GetPCNode->NodePosX = BaseX;
        GetPCNode->NodePosY = BaseY;
        GetPCNode->NodeComment = PCAnchor;
        GetPCNode->bCommentBubbleVisible = true;
        EventGraph->AddNode(GetPCNode, false, false);
        GetPCNode->CreateNewGuid();
        GetPCNode->PostPlacedNewNode();
        GetPCNode->AllocateDefaultPins();

        // --- Node 2: USubsystemBlueprintLibrary::GetLocalPlayerSubSystemFromPlayerController ---
        // (This is exactly what UK2Node_GetSubsystemFromPC compiles to at BP compile time.
        //  We call the function directly because the K2Node class isn't exported from
        //  BlueprintGraph in UE 5.4.)
        UClass* SubLibClass = USubsystemBlueprintLibrary::StaticClass();
        UFunction* GetSubFunc = SubLibClass->FindFunctionByName(FName("GetLocalPlayerSubSystemFromPlayerController"));
        if (!GetSubFunc) return JsonError(TEXT("wire_imc_subscribe"), TEXT("getsubsystem_function_missing"), TEXT(""));

        UK2Node_CallFunction* GetSubNode = NewObject<UK2Node_CallFunction>(EventGraph);
        GetSubNode->SetFlags(RF_Transactional);
        GetSubNode->FunctionReference.SetExternalMember(GetSubFunc->GetFName(), SubLibClass);
        GetSubNode->NodePosX = BaseX + 350;
        GetSubNode->NodePosY = BaseY;
        GetSubNode->NodeComment = SubAnchor;
        GetSubNode->bCommentBubbleVisible = true;
        EventGraph->AddNode(GetSubNode, false, false);
        GetSubNode->CreateNewGuid();
        GetSubNode->PostPlacedNewNode();
        GetSubNode->AllocateDefaultPins();

        // Set Class param default to UEnhancedInputLocalPlayerSubsystem.
        // CRITICAL: must go through Schema so PinDefaultValueChanged fires and
        // the ReturnValue pin retypes from ULocalPlayerSubsystem → UEnhancedInputLocalPlayerSubsystem
        // (this function has meta=(DeterminesOutputType="Class")). Direct assignment skips that.
        if (UEdGraphPin* ClassPin = GetSubNode->FindPin(FName("Class")))
        {
            Schema->TrySetDefaultObject(*ClassPin,
                UEnhancedInputLocalPlayerSubsystem::StaticClass(), /*bMarkAsModified*/ true);
        }

        // --- Node 3: Cast to UEnhancedInputLocalPlayerSubsystem (v6.0.2 fix) ---
        UClass* EISubsystemClass = UEnhancedInputLocalPlayerSubsystem::StaticClass();
        UK2Node_DynamicCast* CastNode = NewObject<UK2Node_DynamicCast>(EventGraph);
        CastNode->SetFlags(RF_Transactional);
        CastNode->TargetType = EISubsystemClass;
        CastNode->NodePosX = BaseX + 700;
        CastNode->NodePosY = BaseY;
        CastNode->NodeComment = CastAnchor;
        CastNode->bCommentBubbleVisible = true;
        EventGraph->AddNode(CastNode, false, false);
        CastNode->CreateNewGuid();
        CastNode->PostPlacedNewNode();
        CastNode->AllocateDefaultPins();

        // BUG-6 fix: UE generates the cast result pin name from the class display name
        // (so "EnhancedInputLocalPlayerSubsystem" → "As Enhanced Input Local Player Subsystem"
        // with spaces). add_cast already overrides this — apply the same normalization
        // here so the wire_imc_subscribe-spawned cast is consistent.
        if (UEdGraphPin* ResultPin = CastNode->GetCastResultPin())
        {
            const FString ConsistentName = FString::Printf(TEXT("As%s"), *EISubsystemClass->GetName());
            ResultPin->PinName = FName(*ConsistentName);
            ResultPin->PinFriendlyName = FText::FromString(ConsistentName);
        }

        // --- Node 4: AddMappingContext ---
        UFunction* AddCtxFunc = EISubsystemClass->FindFunctionByName(FName("AddMappingContext"));
        if (!AddCtxFunc) return JsonError(TEXT("wire_imc_subscribe"), TEXT("addmappingcontext_missing"), TEXT(""));

        UK2Node_CallFunction* AddCtxNode = NewObject<UK2Node_CallFunction>(EventGraph);
        AddCtxNode->SetFlags(RF_Transactional);
        AddCtxNode->FunctionReference.SetExternalMember(AddCtxFunc->GetFName(), EISubsystemClass);
        AddCtxNode->NodePosX = BaseX + 1050;
        AddCtxNode->NodePosY = BaseY;
        AddCtxNode->NodeComment = AddAnchor;
        AddCtxNode->bCommentBubbleVisible = true;
        EventGraph->AddNode(AddCtxNode, false, false);
        AddCtxNode->CreateNewGuid();
        AddCtxNode->PostPlacedNewNode();
        AddCtxNode->AllocateDefaultPins();

        // Set defaults on AddMappingContext: MappingContext = IMC, Priority = N.
        // Same schema-based approach (notifications + mark-modified).
        if (UEdGraphPin* MCPin = AddCtxNode->FindPin(FName("MappingContext")))
        {
            Schema->TrySetDefaultObject(*MCPin, IMC, /*bMarkAsModified*/ true);
        }
        if (UEdGraphPin* PriorityPin = AddCtxNode->FindPin(FName("Priority")))
        {
            Schema->TrySetDefaultValue(*PriorityPin, FString::Printf(TEXT("%d"), Priority), /*bMarkAsModified*/ true);
        }

        // --- Connect everything (v6.0.2 reroute: exec goes through Cast) ---

        // v6.0.3 fix (P6) + BUG-5 fix (v7.1.1): BeginPlay.then is single-output by K2
        // convention. If the user has an existing BeginPlay chain (e.g. EnableInput),
        // naive TryCreateConnection would sever it (CONNECT_RESPONSE_BREAK_OTHERS_A).
        //
        // Walk strategy: try to find a leaf of the existing .then chain. If the walk
        // fails to find one (e.g. a mid-chain node has no "then" pin), fall back to
        // splice-mode: snapshot the original link, allow the overwrite, then rejoin
        // the original next at the tail of our subscribe chain. Either way the user's
        // chain is preserved.
        UEdGraphPin* OriginalNext = (BeginPlayThen->LinkedTo.Num() > 0)
            ? BeginPlayThen->LinkedTo[0] : nullptr;
        UEdGraphPin* InsertExecAfter = BeginPlayThen;
        bool bWalkFoundLeaf = false;

        if (OriginalNext != nullptr)
        {
            UEdGraphPin* CurrentLink = OriginalNext;
            int32 SafetyCounter = 32;   // prevent infinite loop on circular graphs
            while (CurrentLink && SafetyCounter-- > 0)
            {
                UEdGraphNode* CurrentNode = CurrentLink->GetOwningNode();
                if (!CurrentNode) break;
                // Use canonical PN_Then constant (lowercase "then") instead of magic string
                UEdGraphPin* NextThen = CurrentNode->FindPin(UEdGraphSchema_K2::PN_Then);
                if (!NextThen)
                {
                    // Node has no .then pin — walk failed, splice-mode will recover below
                    break;
                }
                if (NextThen->LinkedTo.Num() == 0)
                {
                    // Found the leaf — append-mode
                    InsertExecAfter = NextThen;
                    bWalkFoundLeaf = true;
                    break;
                }
                CurrentLink = NextThen->LinkedTo[0];
            }
        }

        // Decide insertion mode:
        // - Append-mode (walk found leaf): InsertExecAfter is the tail of existing chain.
        //   Just TryCreateConnection; the existing chain is upstream of us, untouched.
        // - Splice-mode (no original chain OR walk failed): InsertExecAfter is BeginPlayThen.
        //   TryCreateConnection will break BeginPlayThen → OriginalNext. We re-attach
        //   OriginalNext at our tail (AddCtx.then) after the chain is built.
        const bool bSpliceMode = !bWalkFoundLeaf && OriginalNext != nullptr;

        // EXEC chain: <insert-point>.then → Cast.execute → Cast.then → AddCtx.execute
        if (UEdGraphPin* CastExec = CastNode->FindPin(FName("execute")))
        {
            Schema->TryCreateConnection(InsertExecAfter, CastExec);
        }
        if (UEdGraphPin* CastThen = CastNode->FindPin(UEdGraphSchema_K2::PN_Then))
        {
            if (UEdGraphPin* AddExec = AddCtxNode->FindPin(FName("execute")))
            {
                Schema->TryCreateConnection(CastThen, AddExec);
            }
        }

        // BUG-5 fix splice-mode recovery: walk failed to find a leaf, so the
        // TryCreateConnection(BeginPlayThen, CastExec) above broke the original chain.
        // Reattach OriginalNext at the end of our subscribe chain so user's chain is preserved.
        if (bSpliceMode)
        {
            if (UEdGraphPin* AddCtxThen = AddCtxNode->FindPin(UEdGraphSchema_K2::PN_Then))
            {
                Schema->TryCreateConnection(AddCtxThen, OriginalNext);
                UE_LOG(LogBlueprintMCP_TCP, Log,
                    TEXT("wire_imc_subscribe: splice-mode — reconnected original chain (next pin owned by %s) at AddCtx.then"),
                    OriginalNext->GetOwningNode() ? *OriginalNext->GetOwningNode()->GetName() : TEXT("?"));
            }
        }

        // DATA chain:
        // GetPC.ReturnValue → GetSubsystem.PlayerController
        if (UEdGraphPin* PCReturn = GetPCNode->FindPin(FName("ReturnValue")))
        {
            if (UEdGraphPin* SubPCIn = GetSubNode->FindPin(FName("PlayerController")))
            {
                Schema->TryCreateConnection(PCReturn, SubPCIn);
            }
        }
        // GetSubsystem.ReturnValue → Cast.Object
        if (UEdGraphPin* SubReturn = GetSubNode->FindPin(FName("ReturnValue")))
        {
            if (UEdGraphPin* CastObject = CastNode->FindPin(FName("Object")))
            {
                Schema->TryCreateConnection(SubReturn, CastObject);
            }
        }
        // Cast result (typed as UEnhancedInputLocalPlayerSubsystem) → AddCtx.self
        if (UEdGraphPin* CastResult = CastNode->GetCastResultPin())
        {
            UEdGraphPin* AddSelf = AddCtxNode->FindPin(UEdGraphSchema_K2::PN_Self);
            if (!AddSelf) AddSelf = AddCtxNode->FindPin(FName("self"));
            if (AddSelf) Schema->TryCreateConnection(CastResult, AddSelf);
        }

        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, false);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"wire_imc_subscribe\",\"anchors_created\":[%s,%s,%s,%s],\"imc_path\":%s,\"priority\":%d,\"saved\":%s}\n"),
            *EscapeJsonString(PCAnchor), *EscapeJsonString(SubAnchor),
            *EscapeJsonString(CastAnchor), *EscapeJsonString(AddAnchor),
            *EscapeJsonString(IMCPath), Priority,
            bSaved ? TEXT("true") : TEXT("false"));
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
        int32 PosX, int32 PosY,
        const FString& GraphName = FString())   // v7.7.1
    {
        check(IsInGameThread());

        if (!IsKnownMacro(MacroType))
        {
            return JsonError(TEXT("add_macro"), TEXT("unknown_macro_type"),
                FString::Printf(TEXT("%s (known: ForEachLoop, ForLoop, WhileLoop, FlipFlop, DoOnce, Gate, IsValid)"), *MacroType));
        }

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (!Blueprint) return JsonError(TEXT("add_macro"), TEXT("blueprint_not_found"), BlueprintPath);
        UEdGraph* EventGraph = ResolveTargetGraph(Blueprint, GraphName);
        if (EventGraph == nullptr) return JsonGraphNotFound(TEXT("add_macro"), GraphName);

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
        int32 PosX, int32 PosY,
        const FString& GraphName = FString())   // v7.7.1
    {
        check(IsInGameThread());

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (!Blueprint) return JsonError(TEXT("add_self_reference"), TEXT("blueprint_not_found"), BlueprintPath);
        UEdGraph* EventGraph = ResolveTargetGraph(Blueprint, GraphName);
        if (EventGraph == nullptr) return JsonGraphNotFound(TEXT("add_self_reference"), GraphName);

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

    FString DeleteNodeOnGameThread(
        const FString& BlueprintPath,
        const FString& AnchorName,
        const FString& GraphName = FString())   // v7.7.1
    {
        check(IsInGameThread());

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (!Blueprint) return JsonError(TEXT("delete_node"), TEXT("blueprint_not_found"), BlueprintPath);
        UEdGraph* EventGraph = ResolveTargetGraph(Blueprint, GraphName);
        if (EventGraph == nullptr) return JsonGraphNotFound(TEXT("delete_node"), GraphName);

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
        const FString& ToPinRef,
        const FString& GraphName = FString())   // v7.7.1
    {
        check(IsInGameThread());

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (!Blueprint) return JsonError(TEXT("disconnect_pins"), TEXT("blueprint_not_found"), BlueprintPath);
        UEdGraph* EventGraph = ResolveTargetGraph(Blueprint, GraphName);
        if (EventGraph == nullptr) return JsonGraphNotFound(TEXT("disconnect_pins"), GraphName);

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
        int32 PosX, int32 PosY,
        const FString& GraphName = FString())   // v7.7
    {
        check(IsInGameThread());

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr)
        {
            return JsonError(TEXT("add_branch"), TEXT("blueprint_not_found"), BlueprintPath);
        }
        UEdGraph* EventGraph = ResolveTargetGraph(Blueprint, GraphName);
        if (EventGraph == nullptr)
        {
            return JsonGraphNotFound(TEXT("add_branch"), GraphName);
        }

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
        int32 PosX, int32 PosY,
        const FString& GraphName = FString())   // v7.7
    {
        check(IsInGameThread());

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr)
        {
            return JsonError(TEXT("add_cast"), TEXT("blueprint_not_found"), BlueprintPath);
        }
        UEdGraph* EventGraph = ResolveTargetGraph(Blueprint, GraphName);
        if (EventGraph == nullptr)
        {
            return JsonGraphNotFound(TEXT("add_cast"), GraphName);
        }

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

        // v6.0.2 P5 + v7.1.1 BUG-6 fix: UE generates the cast result pin name from the
        // class display name (with spaces inserted between camelCase words). Override to
        // a stable identifier-safe form: "As<ClassName>" (no spaces). Also override
        // PinFriendlyName so ReconstructNode / re-display doesn't regenerate the spaced form.
        if (UEdGraphPin* ResultPin = NewNode->GetCastResultPin())
        {
            const FString ConsistentName = FString::Printf(TEXT("As%s"), *TargetClass->GetName());
            ResultPin->PinName = FName(*ConsistentName);
            ResultPin->PinFriendlyName = FText::FromString(ConsistentName);
        }

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

            // v6.0.2 P4 fix: surface default values for ALL pin storage forms
            if (Pin->Direction == EGPD_Input
                && Pin->PinType.PinCategory != UEdGraphSchema_K2::PC_Exec)
            {
                if (!Pin->DefaultValue.IsEmpty())
                {
                    // Primitive defaults (string / int / float / bool / structs in text form)
                    Writer->WriteValue(TEXT("default"), Pin->DefaultValue);
                }
                else if (Pin->DefaultObject)
                {
                    // Object / class / asset reference defaults — path to the referenced UObject
                    Writer->WriteValue(TEXT("default"), Pin->DefaultObject->GetPathName());
                }
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

        // BUG-3 fix (b): functions { "MyFunc": { "anchors": {...}, "connections": [...] } }
        // So callers can inspect function-body graphs (created via add_function), not just EventGraph.
        Writer->WriteObjectStart(TEXT("functions"));
        for (UEdGraph* FuncGraph : Blueprint->FunctionGraphs)
        {
            if (FuncGraph == nullptr) continue;
            Writer->WriteObjectStart(*FuncGraph->GetName());

            // Per-graph anchor map
            TMap<const UEdGraphNode*, FString> FuncNodeToAnchor;
            for (UEdGraphNode* Node : FuncGraph->Nodes)
            {
                if (Node != nullptr) FuncNodeToAnchor.Add(Node, DeriveAnchorForNode(Node));
            }

            Writer->WriteObjectStart(TEXT("anchors"));
            for (const TPair<const UEdGraphNode*, FString>& Kvp : FuncNodeToAnchor)
            {
                Writer->WriteIdentifierPrefix(*Kvp.Value);
                WriteNodeAnchor(Writer, Kvp.Key);
            }
            Writer->WriteObjectEnd();

            Writer->WriteArrayStart(TEXT("connections"));
            for (const UEdGraphNode* Node : FuncGraph->Nodes)
            {
                if (Node == nullptr) continue;
                const FString FromAnchor = FuncNodeToAnchor.FindRef(Node);
                for (const UEdGraphPin* Pin : Node->Pins)
                {
                    if (Pin->Direction != EGPD_Output) continue;
                    for (const UEdGraphPin* Linked : Pin->LinkedTo)
                    {
                        if (Linked == nullptr || Linked->GetOwningNode() == nullptr) continue;
                        const FString ToAnchor = FuncNodeToAnchor.FindRef(Linked->GetOwningNode());
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
            Writer->WriteArrayEnd();

            Writer->WriteObjectEnd();   // function obj end
        }
        Writer->WriteObjectEnd();   // functions obj end

        // variables [{name, type, subcategory, container}]  — v6.0.2 P4: add container info
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
            // Container info: none / array / set / map
            const TCHAR* ContainerStr = TEXT("none");
            switch (Var.VarType.ContainerType)
            {
                case EPinContainerType::Array: ContainerStr = TEXT("array"); break;
                case EPinContainerType::Set:   ContainerStr = TEXT("set");   break;
                case EPinContainerType::Map:   ContainerStr = TEXT("map");   break;
                default: break;
            }
            if (Var.VarType.ContainerType != EPinContainerType::None)
            {
                Writer->WriteValue(TEXT("container"), ContainerStr);
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

    // ===== v9.7.0 — Level / instance manipulation =====
    //
    // Closes feature-request gaps #2/#3/#6 from the 2026-05-21 review:
    //   list_level_actors  — read what's in the level (LLM is no longer "blind")
    //   get_actor_transform — read position of a spawned instance
    //   set_actor_transform — move an instance (don't re-spawn → no duplicates)
    //   set_actor_property  — set per-instance property (NOT the BP CDO);
    //                         supports another actor's name for AActor refs
    //   delete_actor        — remove from level
    //
    // Actor lookup uses GetName() OR GetActorLabel() — both work, since
    // spawn_actor returns GetName() but Outliner shows GetActorLabel().

    /** Find an actor in the editor world by GetName() or GetActorLabel(). */
    AActor* FindActorByNameOrLabel(const FString& NameOrLabel)
    {
        if (GEditor == nullptr) return nullptr;
        UEditorActorSubsystem* AS = GEditor->GetEditorSubsystem<UEditorActorSubsystem>();
        if (AS == nullptr) return nullptr;
        TArray<AActor*> All = AS->GetAllLevelActors();
        for (AActor* A : All)
        {
            if (A == nullptr) continue;
            if (A->GetName() == NameOrLabel || A->GetActorLabel() == NameOrLabel)
                return A;
        }
        return nullptr;
    }

    FString ListLevelActorsOnGameThread(
        const FString& ClassFilter,
        const FString& NameContains,
        int32 MaxResults)
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("list_level_actors");

        if (GEditor == nullptr)
            return JsonError(CmdName, TEXT("no_editor"), TEXT("GEditor null"));
        UEditorActorSubsystem* AS = GEditor->GetEditorSubsystem<UEditorActorSubsystem>();
        if (AS == nullptr)
            return JsonError(CmdName, TEXT("no_actor_subsystem"));

        // Resolve class filter — accepts bare class name OR /Script/Module.Class
        UClass* FilterClass = nullptr;
        if (!ClassFilter.IsEmpty())
        {
            if (ClassFilter.StartsWith(TEXT("/Script/")))
            {
                FilterClass = LoadObject<UClass>(nullptr, *ClassFilter);
            }
            else
            {
                for (TObjectIterator<UClass> It; It; ++It)
                {
                    if (It->GetName() == ClassFilter)
                    {
                        FilterClass = *It;
                        break;
                    }
                }
            }
            if (FilterClass == nullptr)
                return JsonError(CmdName, TEXT("class_not_found"), ClassFilter);
        }

        TArray<AActor*> All = AS->GetAllLevelActors();

        FString OutJson;
        TSharedRef<TJsonWriter<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>> Writer =
            TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&OutJson);
        Writer->WriteObjectStart();
        Writer->WriteValue(TEXT("ok"), true);
        Writer->WriteValue(TEXT("command"), CmdName);
        Writer->WriteValue(TEXT("class_filter"), ClassFilter);
        Writer->WriteArrayStart(TEXT("actors"));

        int32 Count = 0;
        for (AActor* A : All)
        {
            if (A == nullptr) continue;
            if (FilterClass != nullptr && !A->IsA(FilterClass)) continue;
            if (!NameContains.IsEmpty()
                && !A->GetName().Contains(NameContains, ESearchCase::IgnoreCase)
                && !A->GetActorLabel().Contains(NameContains, ESearchCase::IgnoreCase))
                continue;

            const FVector Loc = A->GetActorLocation();
            Writer->WriteObjectStart();
            Writer->WriteValue(TEXT("name"), A->GetName());
            Writer->WriteValue(TEXT("label"), A->GetActorLabel());
            Writer->WriteValue(TEXT("class"), A->GetClass()->GetName());
            Writer->WriteArrayStart(TEXT("location"));
            Writer->WriteValue(Loc.X);
            Writer->WriteValue(Loc.Y);
            Writer->WriteValue(Loc.Z);
            Writer->WriteArrayEnd();
            Writer->WriteObjectEnd();

            ++Count;
            if (MaxResults > 0 && Count >= MaxResults) break;
        }
        Writer->WriteArrayEnd();
        Writer->WriteValue(TEXT("count"), Count);
        Writer->WriteObjectEnd();
        Writer->Close();
        return OutJson + TEXT("\n");
    }

    FString GetActorTransformOnGameThread(const FString& ActorName)
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("get_actor_transform");
        AActor* A = FindActorByNameOrLabel(ActorName);
        if (A == nullptr)
            return JsonError(CmdName, TEXT("actor_not_found"), ActorName);

        const FTransform& T = A->GetActorTransform();
        const FVector Loc = T.GetLocation();
        const FRotator Rot = T.Rotator();
        const FVector Scale = T.GetScale3D();
        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"get_actor_transform\",\"actor\":%s,\"label\":%s,\"class\":%s,")
            TEXT("\"location\":[%f,%f,%f],\"rotation\":[%f,%f,%f],\"scale\":[%f,%f,%f]}\n"),
            *EscapeJsonString(A->GetName()),
            *EscapeJsonString(A->GetActorLabel()),
            *EscapeJsonString(A->GetClass()->GetName()),
            Loc.X, Loc.Y, Loc.Z,
            Rot.Pitch, Rot.Yaw, Rot.Roll,
            Scale.X, Scale.Y, Scale.Z);
    }

    FString SetActorTransformOnGameThread(
        const FString& ActorName,
        FVector Loc, FRotator Rot, FVector Scale,
        bool bSetLoc, bool bSetRot, bool bSetScale)
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("set_actor_transform");
        AActor* A = FindActorByNameOrLabel(ActorName);
        if (A == nullptr)
            return JsonError(CmdName, TEXT("actor_not_found"), ActorName);

        FTransform NewT = A->GetActorTransform();
        if (bSetLoc)   NewT.SetLocation(Loc);
        if (bSetRot)   NewT.SetRotation(Rot.Quaternion());
        if (bSetScale) NewT.SetScale3D(Scale);

        A->Modify();
        const bool bMoved = A->SetActorTransform(NewT, /*bSweep*/ false, nullptr, ETeleportType::TeleportPhysics);

        // Mark level package dirty so save_all persists the change.
        if (A->GetLevel() != nullptr && A->GetLevel()->GetOutermost() != nullptr)
            A->GetLevel()->GetOutermost()->MarkPackageDirty();

        const FVector OutLoc = NewT.GetLocation();
        const FRotator OutRot = NewT.Rotator();
        const FVector OutScale = NewT.GetScale3D();
        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"set_actor_transform\",\"actor\":%s,\"moved\":%s,")
            TEXT("\"location\":[%f,%f,%f],\"rotation\":[%f,%f,%f],\"scale\":[%f,%f,%f]}\n"),
            *EscapeJsonString(A->GetName()),
            bMoved ? TEXT("true") : TEXT("false"),
            OutLoc.X, OutLoc.Y, OutLoc.Z,
            OutRot.Pitch, OutRot.Yaw, OutRot.Roll,
            OutScale.X, OutScale.Y, OutScale.Z);
    }

    /**
     * Set a property on a level actor INSTANCE (not the BP CDO — see
     * v7's set_component_property for that). For AActor-typed properties,
     * Value can be another actor's name/label — resolved before the
     * asset-path fallback. Per the feature-request doc this is the "set
     * PortalA.LinkedPortal = PortalB" case.
     */
    FString SetActorPropertyOnGameThread(
        const FString& ActorName,
        const FString& PropertyPath,
        const FString& Value)
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("set_actor_property");
        AActor* A = FindActorByNameOrLabel(ActorName);
        if (A == nullptr)
            return JsonError(CmdName, TEXT("actor_not_found"), ActorName);

        void* ValuePtr = nullptr;
        FProperty* LeafProp = WalkPropertyPath(A->GetClass(), A, PropertyPath, ValuePtr);
        if (LeafProp == nullptr || ValuePtr == nullptr)
            return JsonError(CmdName, TEXT("property_not_found"),
                FString::Printf(TEXT("Actor '%s' (class %s) has no property '%s'"),
                    *ActorName, *A->GetClass()->GetName(), *PropertyPath));

        A->PreEditChange(LeafProp);

        FString ResolvedValueStr;
        FString ErrorDetail;
        bool bSuccess = false;

        if (FObjectProperty* ObjProp = CastField<FObjectProperty>(LeafProp))
        {
            UObject* Ref = nullptr;
            const bool bClear = Value.IsEmpty()
                || Value.Equals(TEXT("None"), ESearchCase::IgnoreCase)
                || Value.Equals(TEXT("null"), ESearchCase::IgnoreCase);
            if (!bClear)
            {
                // v9.7.0 — for AActor-typed properties, try another level actor first.
                if (ObjProp->PropertyClass != nullptr && ObjProp->PropertyClass->IsChildOf(AActor::StaticClass()))
                {
                    AActor* OtherActor = FindActorByNameOrLabel(Value);
                    if (OtherActor != nullptr && OtherActor->IsA(ObjProp->PropertyClass))
                        Ref = OtherActor;
                }
                // Fall back: asset path
                if (Ref == nullptr)
                    Ref = LoadObject<UObject>(nullptr, *Value);
                if (Ref == nullptr)
                    ErrorDetail = FString::Printf(
                        TEXT("Cannot resolve '%s' (tried as actor name + asset path)"), *Value);
                else if (!Ref->IsA(ObjProp->PropertyClass))
                {
                    ErrorDetail = FString::Printf(
                        TEXT("'%s' is %s but property expects %s"),
                        *Value, *Ref->GetClass()->GetName(), *ObjProp->PropertyClass->GetName());
                    Ref = nullptr;
                }
            }
            if (ErrorDetail.IsEmpty())
            {
                ObjProp->SetObjectPropertyValue(ValuePtr, Ref);
                ResolvedValueStr = (Ref != nullptr) ? Ref->GetPathName() : TEXT("None");
                bSuccess = true;
            }
        }
        else if (FClassProperty* ClassProp = CastField<FClassProperty>(LeafProp))
        {
            UClass* Class = nullptr;
            const bool bClear = Value.IsEmpty() || Value.Equals(TEXT("None"), ESearchCase::IgnoreCase);
            if (!bClear)
            {
                Class = LoadObject<UClass>(nullptr, *Value);
                if (Class == nullptr)
                    ErrorDetail = FString::Printf(TEXT("Class not found: %s"), *Value);
                else if (ClassProp->MetaClass != nullptr && !Class->IsChildOf(ClassProp->MetaClass))
                {
                    ErrorDetail = FString::Printf(TEXT("'%s' is not a subclass of %s"),
                        *Value, *ClassProp->MetaClass->GetName());
                    Class = nullptr;
                }
            }
            if (ErrorDetail.IsEmpty())
            {
                ClassProp->SetObjectPropertyValue(ValuePtr, Class);
                ResolvedValueStr = (Class != nullptr) ? Class->GetPathName() : TEXT("None");
                bSuccess = true;
            }
        }
        else
        {
            FString NormalizedValue = Value;
            if (FStructProperty* StructProp = CastField<FStructProperty>(LeafProp))
            {
                if (IsSupportedStructForDefault(StructProp->Struct))
                    NormalizedValue = FormatStructDefault(StructProp->Struct, Value);
            }
            const TCHAR* Buffer = *NormalizedValue;
            const TCHAR* Result = LeafProp->ImportText_Direct(Buffer, ValuePtr, /*OwnerObject*/ A, PPF_None);
            if (Result == nullptr)
                ErrorDetail = FString::Printf(TEXT("Failed to parse '%s' for property '%s' (type %s)"),
                    *NormalizedValue, *PropertyPath, *LeafProp->GetClass()->GetName());
            else
            {
                ResolvedValueStr = NormalizedValue;
                bSuccess = true;
            }
        }

        if (!bSuccess)
            return JsonError(CmdName, TEXT("set_failed"), ErrorDetail);

        FPropertyChangedEvent ChangeEvent(LeafProp, EPropertyChangeType::ValueSet);
        A->PostEditChangeProperty(ChangeEvent);
        if (A->GetLevel() != nullptr && A->GetLevel()->GetOutermost() != nullptr)
            A->GetLevel()->GetOutermost()->MarkPackageDirty();

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"set_actor_property\",\"actor\":%s,\"property\":%s,\"resolved_value\":%s}\n"),
            *EscapeJsonString(A->GetName()),
            *EscapeJsonString(PropertyPath),
            *EscapeJsonString(ResolvedValueStr));
    }

    FString DeleteActorOnGameThread(const FString& ActorName)
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("delete_actor");
        AActor* A = FindActorByNameOrLabel(ActorName);
        if (A == nullptr)
            return JsonError(CmdName, TEXT("actor_not_found"), ActorName);
        if (GEditor == nullptr)
            return JsonError(CmdName, TEXT("no_editor"));
        UEditorActorSubsystem* AS = GEditor->GetEditorSubsystem<UEditorActorSubsystem>();
        if (AS == nullptr)
            return JsonError(CmdName, TEXT("no_actor_subsystem"));

        const FString DeletedName = A->GetName();
        const bool bOk = AS->DestroyActor(A);
        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"delete_actor\",\"actor\":%s,\"destroyed\":%s}\n"),
            *EscapeJsonString(DeletedName),
            bOk ? TEXT("true") : TEXT("false"));
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
        const FString& ToPinRef,
        const FString& GraphName = FString())   // v7.7
    {
        check(IsInGameThread());

        // 1. Load BP + target graph (v7.7)
        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr)
        {
            return JsonError(TEXT("connect_pins"), TEXT("blueprint_not_found"), BlueprintPath);
        }
        UEdGraph* EventGraph = ResolveTargetGraph(Blueprint, GraphName);
        if (EventGraph == nullptr)
        {
            return JsonGraphNotFound(TEXT("connect_pins"), GraphName);
        }

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
    // ===== v9.1.0 — Discovery tools =====
    //
    // Closes the "what's in this project?" introspection gap. LLMs previously had
    // to guess asset paths (e.g. /Engine/Mannequin/Mesh/SK_Mannequin_Skeleton vs.
    // /Game/FirstPersonArms/.../SK_Mannequin_Arms_Skeleton) which produced false
    // skeleton_not_found errors. These tools let LLMs probe the actual project state.

    /**
     * Shared core: list assets via IAssetRegistry, optionally filtered by class.
     * Returns ready-to-send JSON (with trailing newline).
     *
     * v9.1.0 fix #1: MUST run on game thread. UE 5.4's IAssetRegistry::GetAssets*
     * asserts IsInGameThread() because "Enumerating in-memory assets... uses
     * non-threadsafe UE::AssetRegistry::Filtering globals". Callers in dispatch
     * branches wrap this in AsyncTask(ENamedThreads::GameThread, ...).
     */
    FString ListAssetsCore(
        const TCHAR* CmdName,
        const FString& FolderPath,
        const FString& AssetClass,         // e.g. "Skeleton", "StaticMesh", "Blueprint". Empty = all.
        bool bRecursive,
        int32 MaxResults)
    {
        check(IsInGameThread());   // IAssetRegistry filtering globals aren't thread-safe
        FAssetRegistryModule& Module = FModuleManager::LoadModuleChecked<FAssetRegistryModule>("AssetRegistry");
        IAssetRegistry& Reg = Module.Get();

        TArray<FAssetData> Results;
        const FName PathName(*(FolderPath.IsEmpty() ? FString(TEXT("/Game")) : FolderPath));

        if (AssetClass.IsEmpty())
        {
            Reg.GetAssetsByPath(PathName, Results, bRecursive);
        }
        else
        {
            // Fast path: try /Script/Engine.Foo (or user-provided /Script/Module.Foo).
            FTopLevelAssetPath ClassPath;
            if (AssetClass.StartsWith(TEXT("/Script/")))
            {
                ClassPath = FTopLevelAssetPath(*AssetClass);
            }
            else
            {
                ClassPath = FTopLevelAssetPath(*FString::Printf(TEXT("/Script/Engine.%s"), *AssetClass));
            }

            TArray<FAssetData> ByClass;
            Reg.GetAssetsByClass(ClassPath, ByClass, /*bSearchSubClasses*/ true);

            // v9.3.0 fallback: if no hits and the user gave a bare class name
            // (no /Script/ prefix), the class might live in a non-Engine
            // module (e.g. /Script/Niagara.NiagaraSystem). Enumerate assets
            // in the path and match by class name. NOTE: this is the
            // exact-class match path — bSearchSubClasses is lost in the
            // fallback, but Niagara/UMG/etc. class names are usually leaf.
            if (ByClass.Num() == 0 && !AssetClass.StartsWith(TEXT("/Script/")))
            {
                TArray<FAssetData> All;
                Reg.GetAssetsByPath(PathName, All, bRecursive);
                const FName ClassFName(*AssetClass);
                for (const FAssetData& Data : All)
                {
                    if (Data.AssetClassPath.GetAssetName() == ClassFName)
                        ByClass.Add(Data);
                }
            }

            // Path-filter the class-scoped results
            const FString FolderNorm = FolderPath.IsEmpty() ? FString(TEXT("/Game")) : FolderPath;
            for (const FAssetData& Data : ByClass)
            {
                const FString PkgPath = Data.PackagePath.ToString();
                if (bRecursive ? PkgPath.StartsWith(FolderNorm) : PkgPath == FolderNorm)
                {
                    Results.Add(Data);
                }
            }
        }

        if (MaxResults > 0 && Results.Num() > MaxResults)
        {
            Results.SetNum(MaxResults, EAllowShrinking::No);
        }

        FString OutJson;
        TSharedRef<TJsonWriter<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>> Writer =
            TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&OutJson);
        Writer->WriteObjectStart();
        Writer->WriteValue(TEXT("ok"), true);
        Writer->WriteValue(TEXT("command"), CmdName);
        Writer->WriteValue(TEXT("folder"), FolderPath.IsEmpty() ? FString(TEXT("/Game")) : FolderPath);
        Writer->WriteValue(TEXT("asset_class"), AssetClass);
        Writer->WriteValue(TEXT("recursive"), bRecursive);
        Writer->WriteValue(TEXT("count"), Results.Num());
        Writer->WriteArrayStart(TEXT("assets"));
        for (const FAssetData& Data : Results)
        {
            Writer->WriteObjectStart();
            Writer->WriteValue(TEXT("name"), Data.AssetName.ToString());
            Writer->WriteValue(TEXT("path"), Data.GetObjectPathString());
            Writer->WriteValue(TEXT("package_path"), Data.PackagePath.ToString());
            Writer->WriteValue(TEXT("class"), Data.AssetClassPath.GetAssetName().ToString());
            Writer->WriteObjectEnd();
        }
        Writer->WriteArrayEnd();
        Writer->WriteObjectEnd();
        Writer->Close();
        return OutJson + TEXT("\n");
    }

    FString ListClassesCore(
        const FString& ParentClassName,
        bool bNativeOnly,
        const FString& NameContains,
        int32 MaxResults)
    {
        check(IsInGameThread());   // UObjectIterator must be game-thread

        UClass* ParentClass = nullptr;
        if (!ParentClassName.IsEmpty())
        {
            // Reuse v7.4's ResolveCastTargetClass — whitelist + qualified fallback
            ParentClass = ResolveCastTargetClass(ParentClassName);
            if (ParentClass == nullptr)
            {
                return JsonError(TEXT("list_classes"), TEXT("parent_class_not_found"), ParentClassName);
            }
        }

        TArray<UClass*> Matches;
        for (TObjectIterator<UClass> It; It; ++It)
        {
            UClass* Cls = *It;
            if (Cls == nullptr) continue;
            if (ParentClass != nullptr && !Cls->IsChildOf(ParentClass)) continue;
            if (bNativeOnly && !Cls->IsNative()) continue;
            // Skip CDO/intermediate cruft
            if (Cls->HasAnyClassFlags(CLASS_NewerVersionExists | CLASS_Deprecated | CLASS_HideDropDown))
                continue;
            if (!NameContains.IsEmpty()
                && !Cls->GetName().Contains(NameContains, ESearchCase::IgnoreCase))
                continue;
            Matches.Add(Cls);
            if (MaxResults > 0 && Matches.Num() >= MaxResults) break;
        }

        FString OutJson;
        TSharedRef<TJsonWriter<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>> Writer =
            TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&OutJson);
        Writer->WriteObjectStart();
        Writer->WriteValue(TEXT("ok"), true);
        Writer->WriteValue(TEXT("command"), TEXT("list_classes"));
        Writer->WriteValue(TEXT("parent_class"), ParentClassName);
        Writer->WriteValue(TEXT("native_only"), bNativeOnly);
        Writer->WriteValue(TEXT("name_contains"), NameContains);
        Writer->WriteValue(TEXT("count"), Matches.Num());
        Writer->WriteArrayStart(TEXT("classes"));
        for (UClass* Cls : Matches)
        {
            Writer->WriteObjectStart();
            Writer->WriteValue(TEXT("name"), Cls->GetName());
            Writer->WriteValue(TEXT("path"), Cls->GetPathName());
            Writer->WriteValue(TEXT("native"), Cls->IsNative());
            const UClass* SuperCls = Cls->GetSuperClass();
            Writer->WriteValue(TEXT("super"), SuperCls ? SuperCls->GetName() : TEXT(""));
            Writer->WriteObjectEnd();
        }
        Writer->WriteArrayEnd();
        Writer->WriteObjectEnd();
        Writer->Close();
        return OutJson + TEXT("\n");
    }

    // ===== v9.0.0 — AnimBlueprint creation =====
    //
    // Opens the Animation Blueprint surface. Only the asset-creation step is in v9.0.0 —
    // state machines, state nodes, transitions, and sequence-player pose configuration
    // are planned for v9.0.x follow-ups. The blank AnimBP can already be opened in the
    // editor and edited manually.

    FString CreateAnimBlueprintOnGameThread(
        const FString& Name,
        const FString& SkeletonPath,
        const FString& Path)
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("create_anim_blueprint");

        // Validate skeleton (required — AnimBP can't exist without one in normal mode)
        USkeleton* TargetSkeleton = LoadObject<USkeleton>(nullptr, *SkeletonPath);
        if (TargetSkeleton == nullptr)
        {
            return JsonError(CmdName, TEXT("skeleton_not_found"),
                FString::Printf(TEXT("%s (try /Engine/Mannequin/Mesh/SK_Mannequin_Skeleton or your project's skeleton)"),
                    *SkeletonPath));
        }

        const FString FullAssetPath = Path / Name;
        if (UEditorAssetLibrary::DoesAssetExist(FullAssetPath))
        {
            return JsonError(CmdName, TEXT("asset_exists"), FullAssetPath);
        }

        // Mirror create_blueprint's pattern: configure factory, call IAssetTools.
        UAnimBlueprintFactory* Factory = NewObject<UAnimBlueprintFactory>();
        Factory->ParentClass = UAnimInstance::StaticClass();
        Factory->TargetSkeleton = TargetSkeleton;
        Factory->bTemplate = false;

        FAssetToolsModule& AssetToolsModule =
            FModuleManager::LoadModuleChecked<FAssetToolsModule>("AssetTools");
        UObject* NewAsset = AssetToolsModule.Get().CreateAsset(
            Name, Path, UAnimBlueprint::StaticClass(), Factory);

        if (NewAsset == nullptr)
        {
            return JsonError(CmdName, TEXT("creation_failed"), FullAssetPath);
        }
        UAnimBlueprint* AnimBP = Cast<UAnimBlueprint>(NewAsset);
        if (AnimBP == nullptr)
        {
            return JsonError(CmdName, TEXT("wrong_asset_type"),
                FString::Printf(TEXT("Created asset is %s, not UAnimBlueprint"),
                    *NewAsset->GetClass()->GetName()));
        }

        const bool bSaved = UEditorAssetLibrary::SaveAsset(FullAssetPath, /*bOnlyIfIsDirty*/ false);
        if (!bSaved)
        {
            UE_LOG(LogBlueprintMCP_TCP, Warning,
                TEXT("create_anim_blueprint: created but save failed (%s)"), *FullAssetPath);
        }

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"create_anim_blueprint\",\"blueprint_path\":%s,\"skeleton\":%s,\"parent_class\":\"AnimInstance\",\"saved\":%s}\n"),
            *EscapeJsonString(FullAssetPath),
            *EscapeJsonString(SkeletonPath),
            bSaved ? TEXT("true") : TEXT("false"));
    }

    // ===== v9.2.0 — AnimGraph state machine authoring =====
    //
    // Builds on v9.0.0's create_anim_blueprint. Provides:
    //   add_anim_state_machine  — spawn UAnimGraphNode_StateMachine in main AnimGraph
    //   add_anim_state          — spawn UAnimStateNode inside a state machine
    //   add_anim_transition     — wire two states via UAnimStateTransitionNode
    //   set_anim_state_pose     — set a state's interior pose to a sequence asset
    //
    // Naming convention: state machines and states are addressed by user-given
    // names, stored as NodeComment (same anchor system as v0+).

    /** Find the main AnimGraph in an AnimBlueprint. Named "AnimGraph" by convention. */
    UEdGraph* FindAnimGraph(UAnimBlueprint* AnimBP)
    {
        if (AnimBP == nullptr) return nullptr;
        for (UEdGraph* G : AnimBP->FunctionGraphs)
        {
            if (G != nullptr && G->GetFName() == FName(TEXT("AnimGraph")))
                return G;
        }
        // Fallback: first function graph (some AnimBP variants name differently)
        return AnimBP->FunctionGraphs.Num() > 0 ? AnimBP->FunctionGraphs[0] : nullptr;
    }

    /** Find an anim graph node by NodeComment in a graph. */
    template<typename TNode>
    TNode* FindAnimNodeByComment(UEdGraph* Graph, const FString& AnchorName)
    {
        if (Graph == nullptr) return nullptr;
        for (UEdGraphNode* N : Graph->Nodes)
        {
            if (TNode* Cast = ::Cast<TNode>(N))
            {
                if (Cast->NodeComment.Equals(AnchorName, ESearchCase::CaseSensitive))
                    return Cast;
            }
        }
        return nullptr;
    }

    FString AddAnimStateMachineOnGameThread(
        const FString& BlueprintPath,
        const FString& StateMachineName,
        int32 PosX, int32 PosY)
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("add_anim_state_machine");

        UAnimBlueprint* AnimBP = LoadObject<UAnimBlueprint>(nullptr, *BlueprintPath);
        if (AnimBP == nullptr)
            return JsonError(CmdName, TEXT("anim_blueprint_not_found"), BlueprintPath);

        UEdGraph* AnimGraph = FindAnimGraph(AnimBP);
        if (AnimGraph == nullptr)
            return JsonError(CmdName, TEXT("no_anim_graph"), BlueprintPath);

        if (FindAnimNodeByComment<UAnimGraphNode_StateMachine>(AnimGraph, StateMachineName) != nullptr)
            return JsonError(CmdName, TEXT("state_machine_exists"), StateMachineName);

        UAnimGraphNode_StateMachine* SMNode = NewObject<UAnimGraphNode_StateMachine>(AnimGraph);
        SMNode->SetFlags(RF_Transactional);
        SMNode->NodePosX = PosX;
        SMNode->NodePosY = PosY;
        SMNode->NodeComment = StateMachineName;
        SMNode->bCommentBubbleVisible = true;

        AnimGraph->AddNode(SMNode, /*bFromUI*/ false, /*bSelectNewNode*/ false);
        SMNode->CreateNewGuid();
        SMNode->PostPlacedNewNode();   // Critical: creates EditorStateMachineGraph + populates default schema nodes
        SMNode->AllocateDefaultPins();

        UAnimationStateMachineGraph* InteriorGraph = SMNode->EditorStateMachineGraph;
        const FString InteriorGraphName = InteriorGraph != nullptr ? InteriorGraph->GetName() : FString();

        FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(AnimBP);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"add_anim_state_machine\",\"state_machine\":%s,\"interior_graph\":%s,\"node_guid\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(StateMachineName),
            *EscapeJsonString(InteriorGraphName),
            *EscapeJsonString(SMNode->NodeGuid.ToString(EGuidFormats::DigitsWithHyphens)),
            bSaved ? TEXT("true") : TEXT("false"));
    }

    FString AddAnimStateOnGameThread(
        const FString& BlueprintPath,
        const FString& StateMachineName,
        const FString& StateName,
        int32 PosX, int32 PosY)
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("add_anim_state");

        UAnimBlueprint* AnimBP = LoadObject<UAnimBlueprint>(nullptr, *BlueprintPath);
        if (AnimBP == nullptr) return JsonError(CmdName, TEXT("anim_blueprint_not_found"), BlueprintPath);
        UEdGraph* AnimGraph = FindAnimGraph(AnimBP);
        if (AnimGraph == nullptr) return JsonError(CmdName, TEXT("no_anim_graph"), BlueprintPath);

        UAnimGraphNode_StateMachine* SMNode = FindAnimNodeByComment<UAnimGraphNode_StateMachine>(AnimGraph, StateMachineName);
        if (SMNode == nullptr)
            return JsonError(CmdName, TEXT("state_machine_not_found"), StateMachineName);
        UAnimationStateMachineGraph* SMGraph = SMNode->EditorStateMachineGraph;
        if (SMGraph == nullptr)
            return JsonError(CmdName, TEXT("no_state_machine_graph"), StateMachineName);

        if (FindAnimNodeByComment<UAnimStateNode>(SMGraph, StateName) != nullptr)
            return JsonError(CmdName, TEXT("state_exists"), StateName);

        UAnimStateNode* StateNode = NewObject<UAnimStateNode>(SMGraph);
        StateNode->SetFlags(RF_Transactional);
        StateNode->NodePosX = PosX;
        StateNode->NodePosY = PosY;
        StateNode->NodeComment = StateName;
        StateNode->bCommentBubbleVisible = true;

        SMGraph->AddNode(StateNode, /*bFromUI*/ false, /*bSelectNewNode*/ false);
        StateNode->CreateNewGuid();
        StateNode->PostPlacedNewNode();   // Creates BoundGraph (interior animation graph) + default pose sink
        StateNode->AllocateDefaultPins();

        UEdGraph* BoundGraph = StateNode->GetBoundGraph();
        const FString BoundGraphName = BoundGraph != nullptr ? BoundGraph->GetName() : FString();

        FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(AnimBP);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"add_anim_state\",\"state\":%s,\"state_machine\":%s,\"bound_graph\":%s,\"node_guid\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(StateName),
            *EscapeJsonString(StateMachineName),
            *EscapeJsonString(BoundGraphName),
            *EscapeJsonString(StateNode->NodeGuid.ToString(EGuidFormats::DigitsWithHyphens)),
            bSaved ? TEXT("true") : TEXT("false"));
    }

    FString AddAnimTransitionOnGameThread(
        const FString& BlueprintPath,
        const FString& StateMachineName,
        const FString& FromStateName,
        const FString& ToStateName)
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("add_anim_transition");

        UAnimBlueprint* AnimBP = LoadObject<UAnimBlueprint>(nullptr, *BlueprintPath);
        if (AnimBP == nullptr) return JsonError(CmdName, TEXT("anim_blueprint_not_found"), BlueprintPath);
        UEdGraph* AnimGraph = FindAnimGraph(AnimBP);
        if (AnimGraph == nullptr) return JsonError(CmdName, TEXT("no_anim_graph"), BlueprintPath);

        UAnimGraphNode_StateMachine* SMNode = FindAnimNodeByComment<UAnimGraphNode_StateMachine>(AnimGraph, StateMachineName);
        if (SMNode == nullptr)
            return JsonError(CmdName, TEXT("state_machine_not_found"), StateMachineName);
        UAnimationStateMachineGraph* SMGraph = SMNode->EditorStateMachineGraph;
        if (SMGraph == nullptr)
            return JsonError(CmdName, TEXT("no_state_machine_graph"), StateMachineName);

        UAnimStateNode* FromState = FindAnimNodeByComment<UAnimStateNode>(SMGraph, FromStateName);
        UAnimStateNode* ToState   = FindAnimNodeByComment<UAnimStateNode>(SMGraph, ToStateName);
        if (FromState == nullptr)
            return JsonError(CmdName, TEXT("from_state_not_found"), FromStateName);
        if (ToState == nullptr)
            return JsonError(CmdName, TEXT("to_state_not_found"), ToStateName);

        // Spawn transition node + wire with the canonical CreateConnections API
        UAnimStateTransitionNode* TransNode = NewObject<UAnimStateTransitionNode>(SMGraph);
        TransNode->SetFlags(RF_Transactional);
        // Position halfway between the two states for visual sanity
        TransNode->NodePosX = (FromState->NodePosX + ToState->NodePosX) / 2;
        TransNode->NodePosY = (FromState->NodePosY + ToState->NodePosY) / 2;
        TransNode->bCommentBubbleVisible = false;

        SMGraph->AddNode(TransNode, /*bFromUI*/ false, /*bSelectNewNode*/ false);
        TransNode->CreateNewGuid();
        TransNode->PostPlacedNewNode();   // Creates BoundGraph for the transition rule (a bool expression)
        TransNode->AllocateDefaultPins();
        // The canonical API: wires output of From + input of To via the transition node
        TransNode->CreateConnections(FromState, ToState);

        FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(AnimBP);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"add_anim_transition\",\"from_state\":%s,\"to_state\":%s,\"state_machine\":%s,\"node_guid\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(FromStateName),
            *EscapeJsonString(ToStateName),
            *EscapeJsonString(StateMachineName),
            *EscapeJsonString(TransNode->NodeGuid.ToString(EGuidFormats::DigitsWithHyphens)),
            bSaved ? TEXT("true") : TEXT("false"));
    }

    FString SetAnimStatePoseOnGameThread(
        const FString& BlueprintPath,
        const FString& StateMachineName,
        const FString& StateName,
        const FString& SequencePath)
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("set_anim_state_pose");

        UAnimBlueprint* AnimBP = LoadObject<UAnimBlueprint>(nullptr, *BlueprintPath);
        if (AnimBP == nullptr) return JsonError(CmdName, TEXT("anim_blueprint_not_found"), BlueprintPath);
        UEdGraph* AnimGraph = FindAnimGraph(AnimBP);
        if (AnimGraph == nullptr) return JsonError(CmdName, TEXT("no_anim_graph"), BlueprintPath);
        UAnimGraphNode_StateMachine* SMNode = FindAnimNodeByComment<UAnimGraphNode_StateMachine>(AnimGraph, StateMachineName);
        if (SMNode == nullptr)
            return JsonError(CmdName, TEXT("state_machine_not_found"), StateMachineName);
        UAnimationStateMachineGraph* SMGraph = SMNode->EditorStateMachineGraph;
        if (SMGraph == nullptr)
            return JsonError(CmdName, TEXT("no_state_machine_graph"), StateMachineName);

        UAnimStateNode* StateNode = FindAnimNodeByComment<UAnimStateNode>(SMGraph, StateName);
        if (StateNode == nullptr)
            return JsonError(CmdName, TEXT("state_not_found"), StateName);

        UEdGraph* BoundGraph = StateNode->GetBoundGraph();
        if (BoundGraph == nullptr)
            return JsonError(CmdName, TEXT("no_bound_graph"), StateName);

        // Validate sequence asset
        UAnimSequence* Sequence = LoadObject<UAnimSequence>(nullptr, *SequencePath);
        if (Sequence == nullptr)
            return JsonError(CmdName, TEXT("sequence_not_found"), SequencePath);

        // Skeleton compatibility check — sequence and AnimBP must share a skeleton
        if (Sequence->GetSkeleton() != AnimBP->TargetSkeleton)
        {
            const FString SeqSkel = Sequence->GetSkeleton() ? Sequence->GetSkeleton()->GetPathName() : FString(TEXT("<null>"));
            const FString BPSkel  = AnimBP->TargetSkeleton ? AnimBP->TargetSkeleton->GetPathName() : FString(TEXT("<null>"));
            return JsonError(CmdName, TEXT("skeleton_mismatch"),
                FString::Printf(TEXT("Sequence skeleton=%s, AnimBP skeleton=%s"), *SeqSkel, *BPSkel));
        }

        // Find or create a SequencePlayer node in the BoundGraph
        UAnimGraphNode_SequencePlayer* PlayerNode = nullptr;
        for (UEdGraphNode* N : BoundGraph->Nodes)
        {
            if (UAnimGraphNode_SequencePlayer* SP = Cast<UAnimGraphNode_SequencePlayer>(N))
            {
                PlayerNode = SP;
                break;
            }
        }

        if (PlayerNode == nullptr)
        {
            PlayerNode = NewObject<UAnimGraphNode_SequencePlayer>(BoundGraph);
            PlayerNode->SetFlags(RF_Transactional);
            PlayerNode->NodePosX = -300;
            PlayerNode->NodePosY = 0;
            BoundGraph->AddNode(PlayerNode, /*bFromUI*/ false, /*bSelectNewNode*/ false);
            PlayerNode->CreateNewGuid();
            PlayerNode->PostPlacedNewNode();
            PlayerNode->AllocateDefaultPins();
        }

        // Set the sequence via the node's anim node struct
        PlayerNode->Node.SetSequence(Sequence);

        // Wire SequencePlayer.Pose → state's pose sink
        UEdGraphPin* SinkInputPin = StateNode->GetPoseSinkPinInsideState();
        UEdGraphPin* PlayerPoseOut = nullptr;
        for (UEdGraphPin* P : PlayerNode->Pins)
        {
            if (P->Direction == EGPD_Output && P->PinType.PinCategory == FName(TEXT("struct")))
            {
                // Pose pin is a struct (FPoseLink). Pick the first output struct pin.
                PlayerPoseOut = P;
                break;
            }
        }
        bool bWired = false;
        if (SinkInputPin != nullptr && PlayerPoseOut != nullptr)
        {
            const UEdGraphSchema* Schema = BoundGraph->GetSchema();
            if (Schema != nullptr)
            {
                Schema->TryCreateConnection(PlayerPoseOut, SinkInputPin);
                bWired = (PlayerPoseOut->LinkedTo.Num() > 0);
            }
        }

        FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(AnimBP);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"set_anim_state_pose\",\"state\":%s,\"state_machine\":%s,\"sequence\":%s,\"wired\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(StateName),
            *EscapeJsonString(StateMachineName),
            *EscapeJsonString(SequencePath),
            bWired ? TEXT("true") : TEXT("false"),
            bSaved ? TEXT("true") : TEXT("false"));
    }

    // ===== v9.3.0 — Niagara door-opener =====
    //
    // Opens the Niagara VFX surface. v9.3.0 ships only the asset-creation step;
    // emitter authoring, module parameters, etc. are planned follow-ups.
    //
    // Implementation notes:
    //
    // 1. UNiagaraSystemFactoryNew is NOT NIAGARAEDITOR_API-exported, so we
    //    cannot link to UNiagaraSystemFactoryNew::StaticClass() directly.
    //    Resolve the UClass at runtime via FindObject and instantiate
    //    through the UFactory base type — IAssetTools::CreateAsset only
    //    needs a valid UFactory*.
    //
    // 2. The factory has a ConfigureProperties() that would pop a modal
    //    asset-browser dialog, but IAssetTools::CreateAsset (the non-dialog
    //    overload) skips it and calls FactoryCreateNew directly. With no
    //    source set, the factory creates a blank system via NewObject +
    //    InitializeSystem(bCreateDefaultNodes=true), which sets up
    //    SystemSpawnScript/SystemUpdateScript + default effect type.

    FString CreateNiagaraSystemOnGameThread(const FString& Name, const FString& Path)
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("create_niagara_system");

        const FString FullAssetPath = Path / Name;
        if (UEditorAssetLibrary::DoesAssetExist(FullAssetPath))
            return JsonError(CmdName, TEXT("asset_exists"), FullAssetPath);

        // Resolve UNiagaraSystemFactoryNew dynamically — see note above.
        UClass* FactoryClass = FindObject<UClass>(
            nullptr, TEXT("/Script/NiagaraEditor.NiagaraSystemFactoryNew"));
        if (FactoryClass == nullptr)
            return JsonError(CmdName, TEXT("niagara_factory_not_found"),
                TEXT("UNiagaraSystemFactoryNew UClass not found. Ensure NiagaraEditor plugin is enabled."));

        UFactory* Factory = NewObject<UFactory>(GetTransientPackage(), FactoryClass);
        if (Factory == nullptr)
            return JsonError(CmdName, TEXT("factory_instantiation_failed"));

        FAssetToolsModule& AssetToolsModule =
            FModuleManager::LoadModuleChecked<FAssetToolsModule>("AssetTools");
        UObject* NewAsset = AssetToolsModule.Get().CreateAsset(
            Name, Path, UNiagaraSystem::StaticClass(), Factory);

        if (NewAsset == nullptr)
            return JsonError(CmdName, TEXT("creation_failed"), FullAssetPath);

        UNiagaraSystem* System = Cast<UNiagaraSystem>(NewAsset);
        if (System == nullptr)
            return JsonError(CmdName, TEXT("wrong_asset_type"),
                FString::Printf(TEXT("Created asset is %s, not UNiagaraSystem"),
                    *NewAsset->GetClass()->GetName()));

        const bool bSaved = UEditorAssetLibrary::SaveAsset(FullAssetPath, /*bOnlyIfIsDirty*/ false);
        if (!bSaved)
        {
            UE_LOG(LogBlueprintMCP_TCP, Warning,
                TEXT("create_niagara_system: created but save failed (%s)"), *FullAssetPath);
        }

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"create_niagara_system\",\"system_path\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(FullAssetPath),
            bSaved ? TEXT("true") : TEXT("false"));
    }

    // ===== v9.4.0 — UMG door-opener =====
    //
    // Opens the UMG (Widget Blueprint) surface. v9.4.0 ships only the
    // asset-creation step; widget tree authoring (Canvas, Button, Text, etc.)
    // is planned for v9.4.x follow-ups.
    //
    // UWidgetBlueprintFactory is MinimalAPI (StaticClass IS exported), so
    // we can NewObject it directly — no FindObject dance needed.

    FString CreateWidgetBlueprintOnGameThread(
        const FString& Name,
        const FString& ParentClassName,   // "" → UUserWidget
        const FString& Path)
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("create_widget_blueprint");

        const FString FullAssetPath = Path / Name;
        if (UEditorAssetLibrary::DoesAssetExist(FullAssetPath))
            return JsonError(CmdName, TEXT("asset_exists"), FullAssetPath);

        // Resolve parent class. Default is UUserWidget; users may pass a
        // custom subclass like "/Game/UI/WBP_MenuBase_C".
        UClass* ParentClass = UUserWidget::StaticClass();
        if (!ParentClassName.IsEmpty())
        {
            UClass* Resolved = FindObject<UClass>(nullptr, *ParentClassName);
            if (Resolved == nullptr)
            {
                // Try as Blueprint Generated Class (BPGC) — common case
                Resolved = LoadObject<UClass>(nullptr, *ParentClassName);
            }
            if (Resolved == nullptr || !Resolved->IsChildOf(UUserWidget::StaticClass()))
            {
                return JsonError(CmdName, TEXT("invalid_parent_class"),
                    FString::Printf(TEXT("%s must derive from UUserWidget"), *ParentClassName));
            }
            ParentClass = Resolved;
        }

        UWidgetBlueprintFactory* Factory = NewObject<UWidgetBlueprintFactory>();
        Factory->BlueprintType = BPTYPE_Normal;
        Factory->ParentClass = ParentClass;

        FAssetToolsModule& AssetToolsModule =
            FModuleManager::LoadModuleChecked<FAssetToolsModule>("AssetTools");
        UObject* NewAsset = AssetToolsModule.Get().CreateAsset(
            Name, Path, UWidgetBlueprint::StaticClass(), Factory);
        if (NewAsset == nullptr)
            return JsonError(CmdName, TEXT("creation_failed"), FullAssetPath);

        UWidgetBlueprint* WidgetBP = Cast<UWidgetBlueprint>(NewAsset);
        if (WidgetBP == nullptr)
            return JsonError(CmdName, TEXT("wrong_asset_type"),
                FString::Printf(TEXT("Created asset is %s, not UWidgetBlueprint"),
                    *NewAsset->GetClass()->GetName()));

        const bool bSaved = UEditorAssetLibrary::SaveAsset(FullAssetPath, /*bOnlyIfIsDirty*/ false);
        if (!bSaved)
        {
            UE_LOG(LogBlueprintMCP_TCP, Warning,
                TEXT("create_widget_blueprint: created but save failed (%s)"), *FullAssetPath);
        }

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"create_widget_blueprint\",\"widget_path\":%s,\"parent_class\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(FullAssetPath),
            *EscapeJsonString(ParentClass->GetPathName()),
            bSaved ? TEXT("true") : TEXT("false"));
    }

    // ===== v9.4.0 — save_all =====
    //
    // Silent save of every dirty package (content + maps). Mirrors what
    // File → Save All does in the UE editor menu, but with no prompts —
    // safe to call right before a kill/restart cycle to avoid the
    // "Save changes?" dialog on next launch.

    FString SaveAllOnGameThread()
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("save_all");

        bool bPackagesNeededSaving = false;
        const bool bOk = FEditorFileUtils::SaveDirtyPackages(
            /*bPromptUserToSave*/ false,
            /*bSaveMapPackages*/ true,
            /*bSaveContentPackages*/ true,
            /*bFastSave*/ false,
            /*bNotifyNoPackagesSaved*/ false,
            /*bCanBeDeclined*/ true,
            &bPackagesNeededSaving);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"save_all\",\"saved\":%s,\"packages_needed_saving\":%s}\n"),
            bOk ? TEXT("true") : TEXT("false"),
            bPackagesNeededSaving ? TEXT("true") : TEXT("false"));
    }

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

    // ===== v7.2 — add_switch / add_sequence / add_make_array / add_select =====

    /**
     * Configure common K2Node fields after construction but before AddNode/AllocateDefaultPins.
     * Shared by v7.2 K2Node-spawn tools.
     */
    template<typename TNode>
    TNode* SpawnK2NodeBare(UEdGraph* EventGraph, const FString& AnchorName, int32 PosX, int32 PosY)
    {
        TNode* NewNode = NewObject<TNode>(EventGraph);
        NewNode->SetFlags(RF_Transactional);
        NewNode->NodePosX = PosX;
        NewNode->NodePosY = PosY;
        NewNode->NodeComment = AnchorName;
        NewNode->bCommentBubbleVisible = true;
        return NewNode;
    }

    /** Finalize: AddNode + GUID + PostPlacedNewNode + AllocateDefaultPins. Shared closing for v7.2. */
    void FinalizeK2Node(UEdGraph* EventGraph, UEdGraphNode* Node)
    {
        EventGraph->AddNode(Node, /*bFromUI*/ false, /*bSelectNewNode*/ false);
        Node->CreateNewGuid();
        Node->PostPlacedNewNode();
        Node->AllocateDefaultPins();
    }

    FString AddSwitchOnGameThread(
        const FString& BlueprintPath,
        const FString& SwitchType,
        const FString& AnchorName,
        int32 PosX, int32 PosY,
        const FString& EnumClass,
        int32 CaseCount,
        const FString& CaseLabels,
        const FString& GraphName = FString())   // v7.7.1
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("add_switch");

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr) return JsonError(CmdName, TEXT("blueprint_not_found"), BlueprintPath);
        UEdGraph* EventGraph = ResolveTargetGraph(Blueprint, GraphName);
        if (EventGraph == nullptr) return JsonGraphNotFound(CmdName, GraphName);

        if (FindNodeByAnchor(EventGraph, AnchorName) != nullptr)
            return JsonError(CmdName, TEXT("anchor_name_exists"), AnchorName);

        UK2Node_Switch* NewNode = nullptr;
        FString NodeTypeName;

        const FString LowerType = SwitchType.ToLower();
        if (LowerType == TEXT("int") || LowerType == TEXT("integer"))
        {
            NewNode = SpawnK2NodeBare<UK2Node_SwitchInteger>(EventGraph, AnchorName, PosX, PosY);
            NodeTypeName = TEXT("K2Node_SwitchInteger");
        }
        else if (LowerType == TEXT("string"))
        {
            UK2Node_SwitchString* StrSwitch = SpawnK2NodeBare<UK2Node_SwitchString>(EventGraph, AnchorName, PosX, PosY);
            TArray<FString> Labels;
            CaseLabels.ParseIntoArray(Labels, TEXT(","), /*InCullEmpty*/ true);
            for (FString& L : Labels)
            {
                const FString Trimmed = L.TrimStartAndEnd();
                if (!Trimmed.IsEmpty()) StrSwitch->PinNames.Add(FName(*Trimmed));
            }
            NewNode = StrSwitch;
            NodeTypeName = TEXT("K2Node_SwitchString");
        }
        else if (LowerType == TEXT("name"))
        {
            UK2Node_SwitchName* NameSwitch = SpawnK2NodeBare<UK2Node_SwitchName>(EventGraph, AnchorName, PosX, PosY);
            TArray<FString> Labels;
            CaseLabels.ParseIntoArray(Labels, TEXT(","), /*InCullEmpty*/ true);
            for (FString& L : Labels)
            {
                const FString Trimmed = L.TrimStartAndEnd();
                if (!Trimmed.IsEmpty()) NameSwitch->PinNames.Add(FName(*Trimmed));
            }
            NewNode = NameSwitch;
            NodeTypeName = TEXT("K2Node_SwitchName");
        }
        else if (LowerType == TEXT("enum"))
        {
            if (EnumClass.IsEmpty())
                return JsonError(CmdName, TEXT("missing_field"),
                    TEXT("enum_class is required when switch_type=\"enum\""));
            UEnum* Enum = LoadObject<UEnum>(nullptr, *EnumClass);
            if (Enum == nullptr)
                return JsonError(CmdName, TEXT("enum_not_found"), EnumClass);
            UK2Node_SwitchEnum* EnumSwitch = SpawnK2NodeBare<UK2Node_SwitchEnum>(EventGraph, AnchorName, PosX, PosY);
            // v6-style workaround: SetEnum(UEnum*) is not BLUEPRINTGRAPH_API exported in UE 5.4,
            // so direct-assign the public Enum field. AllocateDefaultPins (in FinalizeK2Node)
            // calls CreateCasePins which reads Enum to generate one case pin per enum value.
            EnumSwitch->Enum = Enum;
            NewNode = EnumSwitch;
            NodeTypeName = TEXT("K2Node_SwitchEnum");
        }
        else
        {
            return JsonError(CmdName, TEXT("unknown_switch_type"),
                FString::Printf(TEXT("'%s' is not one of: int, string, name, enum"), *SwitchType));
        }

        FinalizeK2Node(EventGraph, NewNode);

        // BUG-4 fix: count existing case output exec pins at runtime rather than
        // assuming UE 5.4's default state. SwitchInteger may create 0 or 1 default
        // case depending on engine version. Skip "Default" pin and exec input.
        if (UK2Node_SwitchInteger* IntSwitch = Cast<UK2Node_SwitchInteger>(NewNode))
        {
            int32 ExistingCases = 0;
            for (UEdGraphPin* P : IntSwitch->Pins)
            {
                if (P->Direction == EGPD_Output
                    && P->PinType.PinCategory == UEdGraphSchema_K2::PC_Exec
                    && !P->PinName.IsNone()
                    && !P->PinName.ToString().Equals(TEXT("Default"), ESearchCase::IgnoreCase))
                {
                    ExistingCases++;
                }
            }
            const int32 ToAdd = FMath::Max(0, CaseCount - ExistingCases);
            for (int32 i = 0; i < ToAdd; ++i)
            {
                IntSwitch->AddPinToSwitchNode();
            }
        }

        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);

        const FString PinsJson = BuildPinsJsonArray(NewNode);
        const FString GuidStr = NewNode->NodeGuid.ToString(EGuidFormats::DigitsWithHyphens);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"add_switch\",\"anchor_name\":%s,\"switch_type\":%s,\"node_type\":\"%s\",\"node_guid\":%s,\"pins\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(AnchorName),
            *EscapeJsonString(SwitchType),
            *NodeTypeName,
            *EscapeJsonString(GuidStr),
            *PinsJson,
            bSaved ? TEXT("true") : TEXT("false"));
    }

    FString AddSequenceOnGameThread(
        const FString& BlueprintPath,
        const FString& AnchorName,
        int32 PosX, int32 PosY,
        int32 ThenCount,
        const FString& GraphName = FString())   // v7.7.1
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("add_sequence");

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr) return JsonError(CmdName, TEXT("blueprint_not_found"), BlueprintPath);
        UEdGraph* EventGraph = ResolveTargetGraph(Blueprint, GraphName);
        if (EventGraph == nullptr) return JsonGraphNotFound(CmdName, GraphName);

        if (FindNodeByAnchor(EventGraph, AnchorName) != nullptr)
            return JsonError(CmdName, TEXT("anchor_name_exists"), AnchorName);

        UK2Node_ExecutionSequence* NewNode = SpawnK2NodeBare<UK2Node_ExecutionSequence>(EventGraph, AnchorName, PosX, PosY);
        FinalizeK2Node(EventGraph, NewNode);

        // Default sequence has 2 "Then 0" / "Then 1" output exec pins.
        // AddInputPin() is misleadingly named — it adds a new "Then N" output exec pin.
        const int32 DefaultThenCount = 2;
        for (int32 i = DefaultThenCount; i < ThenCount; ++i)
        {
            NewNode->AddInputPin();
        }

        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);

        const FString PinsJson = BuildPinsJsonArray(NewNode);
        const FString GuidStr = NewNode->NodeGuid.ToString(EGuidFormats::DigitsWithHyphens);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"add_sequence\",\"anchor_name\":%s,\"then_count\":%d,\"node_guid\":%s,\"pins\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(AnchorName),
            FMath::Max(ThenCount, DefaultThenCount),
            *EscapeJsonString(GuidStr),
            *PinsJson,
            bSaved ? TEXT("true") : TEXT("false"));
    }

    FString AddMakeArrayOnGameThread(
        const FString& BlueprintPath,
        const FString& AnchorName,
        int32 PosX, int32 PosY,
        int32 NumInputs,
        const FString& GraphName = FString())   // v7.7.1
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("add_make_array");

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr) return JsonError(CmdName, TEXT("blueprint_not_found"), BlueprintPath);
        UEdGraph* EventGraph = ResolveTargetGraph(Blueprint, GraphName);
        if (EventGraph == nullptr) return JsonGraphNotFound(CmdName, GraphName);

        if (FindNodeByAnchor(EventGraph, AnchorName) != nullptr)
            return JsonError(CmdName, TEXT("anchor_name_exists"), AnchorName);

        UK2Node_MakeArray* NewNode = SpawnK2NodeBare<UK2Node_MakeArray>(EventGraph, AnchorName, PosX, PosY);
        FinalizeK2Node(EventGraph, NewNode);

        // Default MakeArray has 1 input pin "[0]". Add more to reach NumInputs.
        const int32 DefaultNumInputs = 1;
        for (int32 i = DefaultNumInputs; i < NumInputs; ++i)
        {
            NewNode->AddInputPin();
        }

        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);

        const FString PinsJson = BuildPinsJsonArray(NewNode);
        const FString GuidStr = NewNode->NodeGuid.ToString(EGuidFormats::DigitsWithHyphens);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"add_make_array\",\"anchor_name\":%s,\"num_inputs\":%d,\"node_guid\":%s,\"pins\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(AnchorName),
            FMath::Max(NumInputs, DefaultNumInputs),
            *EscapeJsonString(GuidStr),
            *PinsJson,
            bSaved ? TEXT("true") : TEXT("false"));
    }

    // ===== v7.3 — add_make_struct / add_break_struct =====

    /** Resolve a user-friendly struct type name to UScriptStruct*. v7.3 whitelist + path fallback. */
    UScriptStruct* ResolveScriptStruct(const FString& Name)
    {
        // Built-in CoreUObject structs (use TBaseStructure for reliability)
        if (Name.Equals(TEXT("Vector"), ESearchCase::IgnoreCase))      return TBaseStructure<FVector>::Get();
        if (Name.Equals(TEXT("Vector2D"), ESearchCase::IgnoreCase))    return TBaseStructure<FVector2D>::Get();
        if (Name.Equals(TEXT("Rotator"), ESearchCase::IgnoreCase))     return TBaseStructure<FRotator>::Get();
        if (Name.Equals(TEXT("Transform"), ESearchCase::IgnoreCase))   return TBaseStructure<FTransform>::Get();
        if (Name.Equals(TEXT("LinearColor"), ESearchCase::IgnoreCase)) return TBaseStructure<FLinearColor>::Get();
        if (Name.Equals(TEXT("Color"), ESearchCase::IgnoreCase))       return TBaseStructure<FColor>::Get();
        if (Name.Equals(TEXT("Quat"), ESearchCase::IgnoreCase))        return TBaseStructure<FQuat>::Get();
        // Note: FBox has no TBaseStructure specialization in UE 5.4. Use the qualified
        // path "/Script/CoreUObject.Box" if you need it (handled by the path fallback below).

        // Engine-defined structs (load by /Script/Engine path)
        if (Name.Equals(TEXT("HitResult"), ESearchCase::IgnoreCase))
            return LoadObject<UScriptStruct>(nullptr, TEXT("/Script/Engine.HitResult"));
        if (Name.Equals(TEXT("OverlapResult"), ESearchCase::IgnoreCase))
            return LoadObject<UScriptStruct>(nullptr, TEXT("/Script/Engine.OverlapResult"));
        if (Name.Equals(TEXT("CollisionQueryParams"), ESearchCase::IgnoreCase))
            return LoadObject<UScriptStruct>(nullptr, TEXT("/Script/Engine.CollisionQueryParams"));

        // Qualified path fallback (e.g., "/Script/Engine.HitResult" or "/Game/Structs/MyCustomStruct")
        if (Name.StartsWith(TEXT("/")))
        {
            if (UScriptStruct* Found = LoadObject<UScriptStruct>(nullptr, *Name)) return Found;
        }

        // Last-resort bare-name lookup
        if (UScriptStruct* Found = FindFirstObject<UScriptStruct>(*Name, EFindFirstObjectOptions::NativeFirst))
        {
            return Found;
        }
        return nullptr;
    }

    // ===== v7.8 — save_blueprint (explicit save) =====

    FString SaveBlueprintOnGameThread(const FString& BlueprintPath)
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("save_blueprint");

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr) return JsonError(CmdName, TEXT("blueprint_not_found"), BlueprintPath);

        // Mark modified first so SaveAsset(bOnlyIfIsDirty=true) would still write; we use
        // bOnlyIfIsDirty=false to force the write regardless.
        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);

        // Try to fetch the on-disk file path for the response (debug aid)
        FString PackagePath = Blueprint->GetOutermost() ? Blueprint->GetOutermost()->GetName() : FString();

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"save_blueprint\",\"blueprint\":%s,\"package\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(BlueprintPath),
            *EscapeJsonString(PackagePath),
            bSaved ? TEXT("true") : TEXT("false"));
    }

    /**
     * BUG-2 helper: structs like FHitResult / FOverlapResult / FRotator declare a
     * `HasNativeBreak` / `HasNativeMake` USTRUCT meta pointing at a native UFunction
     * (e.g. `/Script/Engine.GameplayStatics.BreakHitResult`). K2Node_BreakStruct /
     * K2Node_MakeStruct on these structs DON'T expose member pins at AllocateDefaultPins
     * time — UE expects the user to use a K2Node_CallFunction on the native function
     * instead, and ExpandNode substitutes at compile time.
     *
     * For LLM authoring we need pins visible immediately, so this helper substitutes
     * the make/break node with a K2Node_CallFunction at spawn time.
     *
     * MetaKey is "HasNativeBreak" or "HasNativeMake"; format of value is a fully
     * qualified function path like "/Script/Engine.GameplayStatics.BreakHitResult".
     */
    UK2Node_CallFunction* SpawnNativeStructFunctionNode(
        UEdGraph* EventGraph,
        UScriptStruct* StructType,
        const FName& MetaKey,
        const FString& AnchorName,
        int32 PosX, int32 PosY,
        FString& OutErrorDetail)
    {
        const FString MetaData = StructType->GetMetaData(MetaKey);
        if (MetaData.IsEmpty())
        {
            OutErrorDetail = FString::Printf(TEXT("Struct '%s' has %s meta but empty value"),
                *StructType->GetName(), *MetaKey.ToString());
            return nullptr;
        }
        // FindObject<UFunction>(nullptr, "/Script/Engine.GameplayStatics.BreakHitResult", true)
        // mirrors K2Node_BreakStruct.cpp:454
        UFunction* NativeFn = FindObject<UFunction>(nullptr, *MetaData, /*bExactClass*/ true);
        if (NativeFn == nullptr)
        {
            OutErrorDetail = FString::Printf(TEXT("Struct '%s' has %s='%s' but function not found"),
                *StructType->GetName(), *MetaKey.ToString(), *MetaData);
            return nullptr;
        }
        UClass* OwnerClass = NativeFn->GetOuterUClass();
        if (OwnerClass == nullptr)
        {
            OutErrorDetail = FString::Printf(TEXT("Native function '%s' has no outer class"), *MetaData);
            return nullptr;
        }

        UK2Node_CallFunction* CallNode = SpawnK2NodeBare<UK2Node_CallFunction>(EventGraph, AnchorName, PosX, PosY);
        CallNode->FunctionReference.SetExternalMember(NativeFn->GetFName(), OwnerClass);
        FinalizeK2Node(EventGraph, CallNode);
        return CallNode;
    }

    FString AddMakeStructOnGameThread(
        const FString& BlueprintPath,
        const FString& StructTypeName,
        const FString& AnchorName,
        int32 PosX, int32 PosY,
        const FString& GraphName = FString())   // v7.7.1
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("add_make_struct");

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr) return JsonError(CmdName, TEXT("blueprint_not_found"), BlueprintPath);
        UEdGraph* EventGraph = ResolveTargetGraph(Blueprint, GraphName);
        if (EventGraph == nullptr) return JsonGraphNotFound(CmdName, GraphName);

        if (FindNodeByAnchor(EventGraph, AnchorName) != nullptr)
            return JsonError(CmdName, TEXT("anchor_name_exists"), AnchorName);

        UScriptStruct* StructType = ResolveScriptStruct(StructTypeName);
        if (StructType == nullptr)
            return JsonError(CmdName, TEXT("unknown_struct_type"), StructTypeName);

        UEdGraphNode* SpawnedNode = nullptr;
        FString NodeTypeName;
        FString NativeFnLabel;   // empty unless we substituted

        // BUG-2 fix: detect HasNativeMake and substitute with K2Node_CallFunction
        if (StructType->HasMetaData(FName(TEXT("HasNativeMake"))))
        {
            FString ErrDetail;
            UK2Node_CallFunction* CallNode = SpawnNativeStructFunctionNode(
                EventGraph, StructType, FName(TEXT("HasNativeMake")),
                AnchorName, PosX, PosY, ErrDetail);
            if (CallNode == nullptr)
                return JsonError(CmdName, TEXT("native_make_unresolved"), ErrDetail);
            SpawnedNode = CallNode;
            NodeTypeName = TEXT("K2Node_CallFunction");
            NativeFnLabel = FString::Printf(TEXT("%s::%s"),
                *CallNode->FunctionReference.GetMemberParentClass()->GetName(),
                *CallNode->FunctionReference.GetMemberName().ToString());
        }
        else
        {
            UK2Node_MakeStruct* NewNode = SpawnK2NodeBare<UK2Node_MakeStruct>(EventGraph, AnchorName, PosX, PosY);
            NewNode->StructType = StructType;  // MUST set before AllocateDefaultPins
            FinalizeK2Node(EventGraph, NewNode);
            SpawnedNode = NewNode;
            NodeTypeName = TEXT("K2Node_MakeStruct");
        }

        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);

        const FString PinsJson = BuildPinsJsonArray(SpawnedNode);
        const FString GuidStr = SpawnedNode->NodeGuid.ToString(EGuidFormats::DigitsWithHyphens);

        if (NativeFnLabel.IsEmpty())
        {
            return FString::Printf(
                TEXT("{\"ok\":true,\"command\":\"add_make_struct\",\"anchor_name\":%s,\"struct_type\":%s,\"node_type\":\"%s\",\"node_guid\":%s,\"pins\":%s,\"saved\":%s}\n"),
                *EscapeJsonString(AnchorName),
                *EscapeJsonString(StructType->GetName()),
                *NodeTypeName,
                *EscapeJsonString(GuidStr),
                *PinsJson,
                bSaved ? TEXT("true") : TEXT("false"));
        }
        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"add_make_struct\",\"anchor_name\":%s,\"struct_type\":%s,\"node_type\":\"%s\",\"native_function\":%s,\"node_guid\":%s,\"pins\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(AnchorName),
            *EscapeJsonString(StructType->GetName()),
            *NodeTypeName,
            *EscapeJsonString(NativeFnLabel),
            *EscapeJsonString(GuidStr),
            *PinsJson,
            bSaved ? TEXT("true") : TEXT("false"));
    }

    FString AddBreakStructOnGameThread(
        const FString& BlueprintPath,
        const FString& StructTypeName,
        const FString& AnchorName,
        int32 PosX, int32 PosY,
        const FString& GraphName = FString())   // v7.7.1
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("add_break_struct");

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr) return JsonError(CmdName, TEXT("blueprint_not_found"), BlueprintPath);
        UEdGraph* EventGraph = ResolveTargetGraph(Blueprint, GraphName);
        if (EventGraph == nullptr) return JsonGraphNotFound(CmdName, GraphName);

        if (FindNodeByAnchor(EventGraph, AnchorName) != nullptr)
            return JsonError(CmdName, TEXT("anchor_name_exists"), AnchorName);

        UScriptStruct* StructType = ResolveScriptStruct(StructTypeName);
        if (StructType == nullptr)
            return JsonError(CmdName, TEXT("unknown_struct_type"), StructTypeName);

        UEdGraphNode* SpawnedNode = nullptr;
        FString NodeTypeName;
        FString NativeFnLabel;

        // BUG-2 fix: detect HasNativeBreak (FHitResult etc.) and use K2Node_CallFunction
        if (StructType->HasMetaData(FName(TEXT("HasNativeBreak"))))
        {
            FString ErrDetail;
            UK2Node_CallFunction* CallNode = SpawnNativeStructFunctionNode(
                EventGraph, StructType, FName(TEXT("HasNativeBreak")),
                AnchorName, PosX, PosY, ErrDetail);
            if (CallNode == nullptr)
                return JsonError(CmdName, TEXT("native_break_unresolved"), ErrDetail);
            SpawnedNode = CallNode;
            NodeTypeName = TEXT("K2Node_CallFunction");
            NativeFnLabel = FString::Printf(TEXT("%s::%s"),
                *CallNode->FunctionReference.GetMemberParentClass()->GetName(),
                *CallNode->FunctionReference.GetMemberName().ToString());
        }
        else
        {
            UK2Node_BreakStruct* NewNode = SpawnK2NodeBare<UK2Node_BreakStruct>(EventGraph, AnchorName, PosX, PosY);
            NewNode->StructType = StructType;
            FinalizeK2Node(EventGraph, NewNode);
            SpawnedNode = NewNode;
            NodeTypeName = TEXT("K2Node_BreakStruct");
        }

        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);

        const FString PinsJson = BuildPinsJsonArray(SpawnedNode);
        const FString GuidStr = SpawnedNode->NodeGuid.ToString(EGuidFormats::DigitsWithHyphens);

        if (NativeFnLabel.IsEmpty())
        {
            return FString::Printf(
                TEXT("{\"ok\":true,\"command\":\"add_break_struct\",\"anchor_name\":%s,\"struct_type\":%s,\"node_type\":\"%s\",\"node_guid\":%s,\"pins\":%s,\"saved\":%s}\n"),
                *EscapeJsonString(AnchorName),
                *EscapeJsonString(StructType->GetName()),
                *NodeTypeName,
                *EscapeJsonString(GuidStr),
                *PinsJson,
                bSaved ? TEXT("true") : TEXT("false"));
        }
        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"add_break_struct\",\"anchor_name\":%s,\"struct_type\":%s,\"node_type\":\"%s\",\"native_function\":%s,\"node_guid\":%s,\"pins\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(AnchorName),
            *EscapeJsonString(StructType->GetName()),
            *NodeTypeName,
            *EscapeJsonString(NativeFnLabel),
            *EscapeJsonString(GuidStr),
            *PinsJson,
            bSaved ? TEXT("true") : TEXT("false"));
    }

    // ===== v7.6 — event dispatchers (multicast delegates) =====

    /**
     * Create a multicast delegate signature (event dispatcher) on a Blueprint.
     * Editor equivalent: "Event Dispatchers" panel → "+" button.
     *
     * Implementation: creates a graph in Blueprint->DelegateSignatureGraphs with
     * a K2Node_FunctionEntry whose user-defined pins become the delegate's params.
     */
    /**
     * v8.1.0 helper: full v7.1.2 dispatcher-creation flow as a reusable function.
     * Does NOT MarkStructurallyModified or compile — caller batches those.
     * Caller MUST have already validated uniqueness + param types.
     */
    bool CreateDispatcherInternal(
        UBlueprint* Blueprint,
        const FName& DispatcherFName,
        const TArray<FString>& ParamNames,
        const TArray<FEdGraphPinType>& ResolvedParamTypes,
        FString& OutError)
    {
        const UEdGraphSchema_K2* K2Schema = GetDefault<UEdGraphSchema_K2>();
        if (K2Schema == nullptr) { OutError = TEXT("no_k2_schema"); return false; }

        // (1) Member variable — becomes FMulticastDelegateProperty after compile
        FEdGraphPinType DelegateType;
        DelegateType.PinCategory = UEdGraphSchema_K2::PC_MCDelegate;
        if (!FBlueprintEditorUtils::AddMemberVariable(Blueprint, DispatcherFName, DelegateType))
        {
            OutError = FString::Printf(TEXT("add_member_variable_failed: %s"), *DispatcherFName.ToString());
            return false;
        }

        // (2) Signature graph
        UEdGraph* NewGraph = FBlueprintEditorUtils::CreateNewGraph(
            Blueprint, DispatcherFName, UEdGraph::StaticClass(), UEdGraphSchema_K2::StaticClass());
        if (NewGraph == nullptr)
        {
            FBlueprintEditorUtils::RemoveMemberVariable(Blueprint, DispatcherFName);
            OutError = FString::Printf(TEXT("graph_create_failed: %s"), *DispatcherFName.ToString());
            return false;
        }
        NewGraph->bEditable = false;

        // (3) Schema setup — creates FunctionEntry/Result, marks editable
        K2Schema->CreateDefaultNodesForGraph(*NewGraph);
        K2Schema->CreateFunctionGraphTerminators(*NewGraph, (UClass*)nullptr);
        K2Schema->AddExtraFunctionFlags(NewGraph, (FUNC_BlueprintCallable | FUNC_BlueprintEvent | FUNC_Public));
        K2Schema->MarkFunctionEntryAsEditable(NewGraph, true);

        // (4) Register graph
        Blueprint->DelegateSignatureGraphs.Add(NewGraph);

        // (5) Find FunctionEntry, add user-defined output pins for each param
        UK2Node_FunctionEntry* EntryNode = nullptr;
        for (UEdGraphNode* N : NewGraph->Nodes)
        {
            if (UK2Node_FunctionEntry* FE = Cast<UK2Node_FunctionEntry>(N))
            {
                EntryNode = FE;
                break;
            }
        }
        if (EntryNode == nullptr)
        {
            OutError = FString::Printf(TEXT("no_function_entry: %s"), *DispatcherFName.ToString());
            return false;
        }
        for (int32 i = 0; i < ParamNames.Num(); ++i)
        {
            EntryNode->CreateUserDefinedPin(FName(*ParamNames[i]), ResolvedParamTypes[i],
                EGPD_Output, /*bUseUniqueName*/ false);
        }

        return true;
    }

    FString AddEventDispatcherOnGameThread(
        const FString& BlueprintPath,
        const FString& DispatcherName,
        const TArray<FString>& ParamNames,
        const TArray<FString>& ParamTypes)
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("add_event_dispatcher");

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr) return JsonError(CmdName, TEXT("blueprint_not_found"), BlueprintPath);

        const FName DispatcherFName(*DispatcherName);

        // Uniqueness: against existing signature graphs AND existing member variables
        for (UEdGraph* G : Blueprint->DelegateSignatureGraphs)
        {
            if (G != nullptr && G->GetFName() == DispatcherFName)
                return JsonError(CmdName, TEXT("dispatcher_exists"), DispatcherName);
        }
        if (FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, DispatcherFName) != INDEX_NONE)
            return JsonError(CmdName, TEXT("variable_name_collision"),
                FString::Printf(TEXT("Member variable '%s' already exists"), *DispatcherName));

        // Pre-validate param types BEFORE creating the graph
        if (ParamNames.Num() != ParamTypes.Num())
            return JsonError(CmdName, TEXT("param_arity_mismatch"),
                FString::Printf(TEXT("params: %d names but %d types"), ParamNames.Num(), ParamTypes.Num()));
        TArray<FEdGraphPinType> ResolvedParamTypes;
        ResolvedParamTypes.Reserve(ParamNames.Num());
        for (int32 i = 0; i < ParamNames.Num(); ++i)
        {
            FEdGraphPinType PinType;
            if (!ResolveVariablePinType(ParamTypes[i], PinType))
                return JsonError(CmdName, TEXT("unknown_param_type"),
                    FString::Printf(TEXT("param '%s' type '%s'"), *ParamNames[i], *ParamTypes[i]));
            ResolvedParamTypes.Add(PinType);
        }

        Blueprint->Modify();

        // Refactored v8.1.0: delegate creation lives in CreateDispatcherInternal so
        // migrate_dispatchers can reuse it for ghost-dispatcher recreation.
        FString InternalError;
        if (!CreateDispatcherInternal(Blueprint, DispatcherFName, ParamNames, ResolvedParamTypes, InternalError))
        {
            return JsonError(CmdName, TEXT("internal_create_failed"), InternalError);
        }

        FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
        FKismetEditorUtilities::CompileBlueprint(Blueprint, EBlueprintCompileOptions::None);

        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"add_event_dispatcher\",\"dispatcher_name\":%s,\"param_count\":%d,\"compiled\":true,\"saved\":%s}\n"),
            *EscapeJsonString(DispatcherName),
            ParamNames.Num(),
            bSaved ? TEXT("true") : TEXT("false"));
    }

    /**
     * v8.0.2 ISSUE-1 fix: scan a Blueprint for old-format event dispatchers (pre-v7.1.2
     * style — signature graph present but no PC_MCDelegate member variable) and
     * back-fill the missing variable. Programmatic upgrade path that avoids the
     * manual delete + recreate dance.
     *
     * Also detects the opposite imbalance (member variable but no signature graph,
     * which shouldn't happen normally but might from interrupted operations) and
     * reports it but doesn't auto-remove the orphan variable — caller can use
     * delete_event_dispatcher.
     */
    FString MigrateDispatchersOnGameThread(
        const FString& BlueprintPath,
        bool bRecreateGhosts)   // v8.1.0: opt-in recreate of "ghost" dispatchers
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("migrate_dispatchers");

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr) return JsonError(CmdName, TEXT("blueprint_not_found"), BlueprintPath);

        TArray<FString> Migrated;
        TArray<FString> AlreadyHealthy;
        TArray<FString> OrphanVariables;          // variable but no signature graph (rare)
        TArray<FString> GhostsDetected;           // v8.1.0: dispatcher names referenced by orphan delegate nodes
        TArray<FString> GhostsRecreated;          // v8.1.0: ghosts we successfully recreated

        // Pass 1: every signature graph should have a matching PC_MCDelegate member variable
        for (UEdGraph* SigGraph : Blueprint->DelegateSignatureGraphs)
        {
            if (SigGraph == nullptr) continue;
            const FName SigName = SigGraph->GetFName();

            if (FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, SigName) != INDEX_NONE)
            {
                AlreadyHealthy.Add(SigName.ToString());
                continue;
            }

            FEdGraphPinType DelegateType;
            DelegateType.PinCategory = UEdGraphSchema_K2::PC_MCDelegate;
            if (FBlueprintEditorUtils::AddMemberVariable(Blueprint, SigName, DelegateType))
            {
                Migrated.Add(SigName.ToString());
                UE_LOG(LogBlueprintMCP_TCP, Log,
                    TEXT("migrate_dispatchers: back-filled member variable '%s' for %s"),
                    *SigName.ToString(), *BlueprintPath);
            }
        }

        // Pass 2: detect orphan PC_MCDelegate variables (no signature graph) — read-only
        for (const FBPVariableDescription& Var : Blueprint->NewVariables)
        {
            if (Var.VarType.PinCategory != UEdGraphSchema_K2::PC_MCDelegate) continue;
            const FName VarName = Var.VarName;
            bool bHasGraph = false;
            for (UEdGraph* SigGraph : Blueprint->DelegateSignatureGraphs)
            {
                if (SigGraph != nullptr && SigGraph->GetFName() == VarName)
                {
                    bHasGraph = true;
                    break;
                }
            }
            if (!bHasGraph)
            {
                OrphanVariables.Add(VarName.ToString());
            }
        }

        // v8.1.0 Pass 3: ghost dispatcher detection. Scan delegate-reference nodes in all
        // graphs and collect names that point at non-existent dispatchers. These survived
        // when the dispatcher itself was deleted; without a matching signature graph or
        // member variable, AllocateDefaultPins resolves to nothing.
        TSet<FName> KnownDispatcherNames;
        for (UEdGraph* G : Blueprint->DelegateSignatureGraphs)
        {
            if (G != nullptr) KnownDispatcherNames.Add(G->GetFName());
        }
        for (const FBPVariableDescription& Var : Blueprint->NewVariables)
        {
            if (Var.VarType.PinCategory == UEdGraphSchema_K2::PC_MCDelegate)
            {
                KnownDispatcherNames.Add(Var.VarName);
            }
        }

        TSet<FName> GhostNames;
        auto ScanGraph = [&](UEdGraph* Graph)
        {
            if (Graph == nullptr) return;
            for (UEdGraphNode* Node : Graph->Nodes)
            {
                if (UK2Node_BaseMCDelegate* DelegateNode = Cast<UK2Node_BaseMCDelegate>(Node))
                {
                    const FName RefName = DelegateNode->DelegateReference.GetMemberName();
                    if (RefName != NAME_None && !KnownDispatcherNames.Contains(RefName))
                    {
                        GhostNames.Add(RefName);
                    }
                }
            }
        };
        for (UEdGraph* G : Blueprint->UbergraphPages)  ScanGraph(G);
        for (UEdGraph* G : Blueprint->FunctionGraphs)  ScanGraph(G);
        // (Macro graphs unlikely to contain delegate refs, but harmless to scan)
        for (UEdGraph* G : Blueprint->MacroGraphs)     ScanGraph(G);

        for (const FName& G : GhostNames) GhostsDetected.Add(G.ToString());

        // v8.1.0 Pass 4: opt-in recreation. Empty signature — caller adds params later
        // via add_custom_event-style flow or manual editor edits. The ghost's old pin
        // types on its caller nodes are NOT inferred; that's a documented limit.
        if (bRecreateGhosts)
        {
            for (const FName& GhostName : GhostNames)
            {
                FString InternalError;
                if (CreateDispatcherInternal(Blueprint, GhostName,
                    /*ParamNames=*/ TArray<FString>(),
                    /*ResolvedParamTypes=*/ TArray<FEdGraphPinType>(),
                    InternalError))
                {
                    GhostsRecreated.Add(GhostName.ToString());
                    UE_LOG(LogBlueprintMCP_TCP, Log,
                        TEXT("migrate_dispatchers: recreated ghost '%s' (empty signature) on %s"),
                        *GhostName.ToString(), *BlueprintPath);
                }
                else
                {
                    UE_LOG(LogBlueprintMCP_TCP, Warning,
                        TEXT("migrate_dispatchers: failed to recreate ghost '%s' on %s: %s"),
                        *GhostName.ToString(), *BlueprintPath, *InternalError);
                }
            }
        }

        // Compile only if we actually changed something
        const bool bDidMutate = (Migrated.Num() > 0) || (GhostsRecreated.Num() > 0);
        bool bCompiled = false;
        bool bSaved = false;
        if (bDidMutate)
        {
            FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
            FKismetEditorUtilities::CompileBlueprint(Blueprint, EBlueprintCompileOptions::None);
            bCompiled = true;
            bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);
        }

        // Build JSON via writer so the string arrays are safely escaped
        FString OutJson;
        TSharedRef<TJsonWriter<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>> Writer =
            TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&OutJson);
        Writer->WriteObjectStart();
        Writer->WriteValue(TEXT("ok"), true);
        Writer->WriteValue(TEXT("command"), TEXT("migrate_dispatchers"));
        Writer->WriteValue(TEXT("blueprint"), BlueprintPath);
        Writer->WriteValue(TEXT("migrated_count"), Migrated.Num());
        Writer->WriteValue(TEXT("already_healthy_count"), AlreadyHealthy.Num());
        Writer->WriteValue(TEXT("orphan_variable_count"), OrphanVariables.Num());
        Writer->WriteValue(TEXT("ghosts_detected_count"), GhostsDetected.Num());
        Writer->WriteValue(TEXT("ghosts_recreated_count"), GhostsRecreated.Num());
        Writer->WriteArrayStart(TEXT("migrated"));
        for (const FString& Name : Migrated) Writer->WriteValue(Name);
        Writer->WriteArrayEnd();
        Writer->WriteArrayStart(TEXT("already_healthy"));
        for (const FString& Name : AlreadyHealthy) Writer->WriteValue(Name);
        Writer->WriteArrayEnd();
        Writer->WriteArrayStart(TEXT("orphan_variables"));
        for (const FString& Name : OrphanVariables) Writer->WriteValue(Name);
        Writer->WriteArrayEnd();
        Writer->WriteArrayStart(TEXT("ghosts_detected"));
        for (const FString& Name : GhostsDetected) Writer->WriteValue(Name);
        Writer->WriteArrayEnd();
        Writer->WriteArrayStart(TEXT("ghosts_recreated"));
        for (const FString& Name : GhostsRecreated) Writer->WriteValue(Name);
        Writer->WriteArrayEnd();
        Writer->WriteValue(TEXT("recreate_ghosts_requested"), bRecreateGhosts);
        Writer->WriteValue(TEXT("compiled"), bCompiled);
        Writer->WriteValue(TEXT("saved"), bSaved);
        Writer->WriteObjectEnd();
        Writer->Close();
        return OutJson + TEXT("\n");
    }

    /**
     * v8.0.1 OPEN-1 fix: delete an event dispatcher (signature graph + member variable)
     * so users can remove dispatchers built by pre-v7.1.2 dylibs (which were missing
     * the member variable and therefore can't be repaired by add_call_dispatcher).
     *
     * Removes whichever of the two pieces is present — old broken dispatchers have
     * only the signature graph; new healthy ones have both. Returns flags so caller
     * can verify what was actually cleaned.
     */
    FString DeleteEventDispatcherOnGameThread(
        const FString& BlueprintPath,
        const FString& DispatcherName)
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("delete_event_dispatcher");

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr) return JsonError(CmdName, TEXT("blueprint_not_found"), BlueprintPath);

        const FName DispatcherFName(*DispatcherName);
        bool bRemovedGraph = false;
        bool bRemovedVariable = false;

        Blueprint->Modify();

        // Remove the signature graph (if any). RemoveGraph handles array removal +
        // cleanup. Iterate backwards in case multiple (corrupt) entries exist.
        for (int32 i = Blueprint->DelegateSignatureGraphs.Num() - 1; i >= 0; --i)
        {
            UEdGraph* G = Blueprint->DelegateSignatureGraphs[i];
            if (G != nullptr && G->GetFName() == DispatcherFName)
            {
                FBlueprintEditorUtils::RemoveGraph(Blueprint, G, EGraphRemoveFlags::Recompile);
                bRemovedGraph = true;
                // Don't break — clean up any duplicates
            }
        }

        // Remove the member variable (present for v7.1.2+ dispatchers, absent for older)
        if (FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, DispatcherFName) != INDEX_NONE)
        {
            FBlueprintEditorUtils::RemoveMemberVariable(Blueprint, DispatcherFName);
            bRemovedVariable = true;
        }

        if (!bRemovedGraph && !bRemovedVariable)
        {
            return JsonError(CmdName, TEXT("dispatcher_not_found"),
                FString::Printf(TEXT("No signature graph or member variable named '%s' in %s"),
                    *DispatcherName, *BlueprintPath));
        }

        FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
        FKismetEditorUtilities::CompileBlueprint(Blueprint, EBlueprintCompileOptions::None);

        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"delete_event_dispatcher\",\"dispatcher_name\":%s,\"removed_graph\":%s,\"removed_variable\":%s,\"compiled\":true,\"saved\":%s}\n"),
            *EscapeJsonString(DispatcherName),
            bRemovedGraph ? TEXT("true") : TEXT("false"),
            bRemovedVariable ? TEXT("true") : TEXT("false"),
            bSaved ? TEXT("true") : TEXT("false"));
    }

    /**
     * Helper for the 3 call/bind/unbind dispatcher node tools.
     * They share: same DelegateReference setup (self-member), same JSON shape.
     */
    template<typename TDelegateNode>
    FString AddDelegateNodeOnGameThread(
        const TCHAR* CmdName,
        const TCHAR* NodeTypeStr,
        const FString& BlueprintPath,
        const FString& DispatcherName,
        const FString& AnchorName,
        int32 PosX, int32 PosY,
        const FString& GraphName = FString())   // v7.7.1
    {
        check(IsInGameThread());

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr) return JsonError(CmdName, TEXT("blueprint_not_found"), BlueprintPath);
        UEdGraph* EventGraph = ResolveTargetGraph(Blueprint, GraphName);
        if (EventGraph == nullptr) return JsonGraphNotFound(CmdName, GraphName);

        if (FindNodeByAnchor(EventGraph, AnchorName) != nullptr)
            return JsonError(CmdName, TEXT("anchor_name_exists"), AnchorName);

        TDelegateNode* NewNode = NewObject<TDelegateNode>(EventGraph);
        NewNode->SetFlags(RF_Transactional);
        NewNode->NodePosX = PosX;
        NewNode->NodePosY = PosY;
        NewNode->NodeComment = AnchorName;
        NewNode->bCommentBubbleVisible = true;

        // Bind to self-member dispatcher. For external delegate properties (e.g.,
        // SomeActor.OnActorBeginOverlap), pass target via connect_pins to the "target"
        // input pin after node creation — SetSelfMember is just the lookup hint.
        NewNode->DelegateReference.SetSelfMember(FName(*DispatcherName));

        EventGraph->AddNode(NewNode, /*bFromUI*/ false, /*bSelectNewNode*/ false);
        NewNode->CreateNewGuid();
        NewNode->PostPlacedNewNode();
        NewNode->AllocateDefaultPins();

        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);

        const FString PinsJson = BuildPinsJsonArray(NewNode);
        const FString GuidStr = NewNode->NodeGuid.ToString(EGuidFormats::DigitsWithHyphens);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":%s,\"anchor_name\":%s,\"dispatcher_name\":%s,\"node_type\":\"%s\",\"node_guid\":%s,\"pins\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(CmdName),
            *EscapeJsonString(AnchorName),
            *EscapeJsonString(DispatcherName),
            NodeTypeStr,
            *EscapeJsonString(GuidStr),
            *PinsJson,
            bSaved ? TEXT("true") : TEXT("false"));
    }

    FString AddSelectOnGameThread(
        const FString& BlueprintPath,
        const FString& AnchorName,
        int32 PosX, int32 PosY,
        int32 NumOptions,
        const FString& GraphName = FString())   // v7.7.1
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("add_select");

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (Blueprint == nullptr) return JsonError(CmdName, TEXT("blueprint_not_found"), BlueprintPath);
        UEdGraph* EventGraph = ResolveTargetGraph(Blueprint, GraphName);
        if (EventGraph == nullptr) return JsonGraphNotFound(CmdName, GraphName);

        if (FindNodeByAnchor(EventGraph, AnchorName) != nullptr)
            return JsonError(CmdName, TEXT("anchor_name_exists"), AnchorName);

        UK2Node_Select* NewNode = SpawnK2NodeBare<UK2Node_Select>(EventGraph, AnchorName, PosX, PosY);
        FinalizeK2Node(EventGraph, NewNode);

        // Note: UE 5.4 UK2Node_Select has no public AddOptionPinToNode() — only
        // RemoveOptionPinToNode(). Default node has 2 option pins; adding more requires
        // the BP editor's "Add Option" context-menu action. Param NumOptions is ignored
        // when > 2 (logged so the user knows). v7.2.x candidate: implement via
        // ReconstructNode + direct NumOptionPins mutation.
        const int32 ActualNumOptions = 2;
        if (NumOptions > ActualNumOptions)
        {
            UE_LOG(LogBlueprintMCP_TCP, Warning,
                TEXT("add_select: requested num_options=%d but UE 5.4 K2Node_Select has no public AddOption — defaulting to 2"),
                NumOptions);
        }

        FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
        const bool bSaved = UEditorAssetLibrary::SaveAsset(BlueprintPath, /*bOnlyIfIsDirty*/ false);

        const FString PinsJson = BuildPinsJsonArray(NewNode);
        const FString GuidStr = NewNode->NodeGuid.ToString(EGuidFormats::DigitsWithHyphens);

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"add_select\",\"anchor_name\":%s,\"num_options\":%d,\"node_guid\":%s,\"pins\":%s,\"saved\":%s}\n"),
            *EscapeJsonString(AnchorName),
            ActualNumOptions,
            *EscapeJsonString(GuidStr),
            *PinsJson,
            bSaved ? TEXT("true") : TEXT("false"));
    }

    // ===== v8.2 — PIE control =====

    /** True iff a PIE session is currently active (not just queued). */
    bool IsPIERunningChecked()
    {
        return GEditor != nullptr && GEditor->PlayWorld != nullptr;
    }

    FString StartPIEOnGameThread()
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("start_pie");
        if (GEditor == nullptr)
            return JsonError(CmdName, TEXT("no_editor"), TEXT("GEditor null"));
        if (IsPIERunningChecked())
            return JsonError(CmdName, TEXT("pie_already_running"), TEXT(""));

        FRequestPlaySessionParams Params;
        Params.WorldType = EPlaySessionWorldType::PlayInEditor;
        GEditor->RequestPlaySession(Params);
        // Note: actual PIE start is queued and processed in next editor tick.
        // is_pie_running will return false for a brief moment after this returns.

        return FString(TEXT("{\"ok\":true,\"command\":\"start_pie\",\"queued\":true}\n"));
    }

    FString StopPIEOnGameThread()
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("stop_pie");
        if (GEditor == nullptr)
            return JsonError(CmdName, TEXT("no_editor"), TEXT("GEditor null"));
        if (!IsPIERunningChecked())
            return JsonError(CmdName, TEXT("pie_not_running"), TEXT(""));

        GEditor->RequestEndPlayMap();
        return FString(TEXT("{\"ok\":true,\"command\":\"stop_pie\",\"queued\":true}\n"));
    }

    FString IsPIERunningOnGameThread()
    {
        check(IsInGameThread());
        const bool bRunning = IsPIERunningChecked();
        const bool bRequestQueued = (GEditor != nullptr) && GEditor->IsPlaySessionRequestQueued();
        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"is_pie_running\",\"running\":%s,\"start_queued\":%s}\n"),
            bRunning ? TEXT("true") : TEXT("false"),
            bRequestQueued ? TEXT("true") : TEXT("false"));
    }

    // ===== v8.3 — Input simulation (v9.9.0 extends with duration + player movement) =====

    FString PiePressKeyOnGameThread(const FString& KeyName, int32 PlayerIndex, float DurationSec)
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("pie_press_key");
        if (GEditor == nullptr)
            return JsonError(CmdName, TEXT("no_editor"), TEXT("GEditor null"));
        UWorld* PlayWorld = GEditor->PlayWorld;
        if (PlayWorld == nullptr)
            return JsonError(CmdName, TEXT("pie_not_running"),
                TEXT("Call start_pie first; wait a tick for it to actually start"));

        APlayerController* PC = UGameplayStatics::GetPlayerController(PlayWorld, PlayerIndex);
        if (PC == nullptr)
            return JsonError(CmdName, TEXT("no_player_controller"),
                FString::Printf(TEXT("player_index=%d"), PlayerIndex));

        const FKey Key = ResolveFKeyWithAliases(KeyName);
        if (!Key.IsValid())
            return JsonError(CmdName, TEXT("invalid_key"),
                FString::Printf(TEXT("%s (try Space, P, LeftMouseButton, F1, ...)"), *KeyName));

        // v9.9.0 — if duration_sec <= 0, behave as before (press+release immediately).
        // Otherwise press now, then schedule the release via FTSTicker after duration_sec.
        // FTSTicker fires on the game thread, no blocking required.
        FInputKeyParams PressParams(Key, IE_Pressed, FVector::ZeroVector, /*bGamepad*/ false);
        PC->InputKey(PressParams);

        const bool bHold = (DurationSec > 0.0f);
        if (!bHold)
        {
            FInputKeyParams ReleaseParams(Key, IE_Released, FVector::ZeroVector, /*bGamepad*/ false);
            PC->InputKey(ReleaseParams);
        }
        else
        {
            // Capture PC + Key by value. Use a weak ptr for PC so if PIE ends
            // before the ticker fires we don't dereference a dangling pointer.
            TWeakObjectPtr<APlayerController> WeakPC(PC);
            const FKey CapturedKey = Key;
            FTSTicker::GetCoreTicker().AddTicker(
                FTickerDelegate::CreateLambda([WeakPC, CapturedKey](float /*Dt*/) -> bool
                {
                    if (APlayerController* PCAlive = WeakPC.Get())
                    {
                        FInputKeyParams RP(CapturedKey, IE_Released, FVector::ZeroVector, false);
                        PCAlive->InputKey(RP);
                    }
                    return false;   // one-shot; do not re-fire
                }),
                DurationSec);
        }

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"pie_press_key\",\"key\":%s,\"player_index\":%d,\"held\":%s,\"duration_sec\":%f}\n"),
            *EscapeJsonString(Key.ToString()),
            PlayerIndex,
            bHold ? TEXT("true") : TEXT("false"),
            DurationSec);
    }

    // v9.9.0 — pie_set_player_location: teleport the controlled pawn.
    FString PieSetPlayerLocationOnGameThread(float X, float Y, float Z, int32 PlayerIndex)
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("pie_set_player_location");
        if (GEditor == nullptr || GEditor->PlayWorld == nullptr)
            return JsonError(CmdName, TEXT("pie_not_running"));

        APlayerController* PC = UGameplayStatics::GetPlayerController(GEditor->PlayWorld, PlayerIndex);
        if (PC == nullptr)
            return JsonError(CmdName, TEXT("no_player_controller"),
                FString::Printf(TEXT("player_index=%d"), PlayerIndex));

        APawn* Pawn = PC->GetPawn();
        if (Pawn == nullptr)
            return JsonError(CmdName, TEXT("no_pawn"), TEXT("PlayerController has no controlled Pawn"));

        const FVector NewLoc(X, Y, Z);
        const bool bOk = Pawn->SetActorLocation(NewLoc, /*bSweep*/ false, nullptr, ETeleportType::TeleportPhysics);

        const FVector Cur = Pawn->GetActorLocation();
        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"pie_set_player_location\",\"player_index\":%d,\"requested\":[%f,%f,%f],\"actual\":[%f,%f,%f],\"moved\":%s}\n"),
            PlayerIndex,
            X, Y, Z,
            Cur.X, Cur.Y, Cur.Z,
            bOk ? TEXT("true") : TEXT("false"));
    }

    // v9.9.0 — pie_move_player: simulated continuous movement input over duration.
    // Each game-thread tick we call Pawn->AddMovementInput(dir, scale). Uses
    // an FTSTicker that re-arms each tick until duration_sec has elapsed.
    // v9.10.0 — bFaceMovement rotates the controller to look down the movement
    // direction first, so first-person characters don't strafe-walk.
    FString PieMovePlayerOnGameThread(float DirX, float DirY, float DirZ, float DurationSec, float Scale, int32 PlayerIndex, bool bFaceMovement)
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("pie_move_player");
        if (GEditor == nullptr || GEditor->PlayWorld == nullptr)
            return JsonError(CmdName, TEXT("pie_not_running"));
        if (DurationSec <= 0.0f)
            return JsonError(CmdName, TEXT("invalid_duration"), TEXT("duration_sec must be > 0"));

        APlayerController* PC = UGameplayStatics::GetPlayerController(GEditor->PlayWorld, PlayerIndex);
        if (PC == nullptr)
            return JsonError(CmdName, TEXT("no_player_controller"),
                FString::Printf(TEXT("player_index=%d"), PlayerIndex));
        APawn* Pawn = PC->GetPawn();
        if (Pawn == nullptr)
            return JsonError(CmdName, TEXT("no_pawn"));

        const FVector Direction(DirX, DirY, DirZ);
        if (Direction.IsNearlyZero())
            return JsonError(CmdName, TEXT("zero_direction"), TEXT("direction vector is (0,0,0)"));
        const FVector NormDir = Direction.GetSafeNormal();

        // v9.10.0 — face the movement direction before moving. Use Yaw only
        // so we don't tilt the camera by mistake (Pitch=0). Roll always 0.
        // For Character pawns with bUseControllerRotationYaw=true (the FP/TP
        // template defaults), the mesh follows the controller's yaw on the
        // next tick.
        FRotator AppliedRotation = FRotator::ZeroRotator;
        if (bFaceMovement)
        {
            AppliedRotation = NormDir.Rotation();
            AppliedRotation.Pitch = 0.0f;
            AppliedRotation.Roll  = 0.0f;
            PC->SetControlRotation(AppliedRotation);
        }

        // Shared elapsed-time counter captured by the ticker.
        TSharedRef<float, ESPMode::ThreadSafe> Elapsed = MakeShared<float, ESPMode::ThreadSafe>(0.0f);
        TWeakObjectPtr<APawn> WeakPawn(Pawn);

        FTSTicker::GetCoreTicker().AddTicker(
            FTickerDelegate::CreateLambda([WeakPawn, NormDir, Scale, DurationSec, Elapsed](float Dt) -> bool
            {
                APawn* PawnAlive = WeakPawn.Get();
                if (PawnAlive == nullptr) return false;  // pawn died → stop

                PawnAlive->AddMovementInput(NormDir, Scale, /*bForce*/ false);

                *Elapsed += Dt;
                return (*Elapsed < DurationSec);   // true = keep ticking, false = stop
            }),
            0.0f);   // start ASAP

        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"pie_move_player\",\"player_index\":%d,\"direction\":[%f,%f,%f],")
            TEXT("\"duration_sec\":%f,\"scale\":%f,\"faced_movement\":%s,\"applied_yaw\":%f,\"queued\":true}\n"),
            PlayerIndex,
            NormDir.X, NormDir.Y, NormDir.Z,
            DurationSec,
            Scale,
            bFaceMovement ? TEXT("true") : TEXT("false"),
            AppliedRotation.Yaw);
    }

    // v9.10.0 — pie_set_player_rotation: sets the PlayerController's
    // ControlRotation, which is the source-of-truth for first-person view
    // direction (mouse-look writes here). On Character pawns with
    // bUseControllerRotationYaw=true, the pawn mesh follows yaw next tick.
    FString PieSetPlayerRotationOnGameThread(float Pitch, float Yaw, float Roll, int32 PlayerIndex)
    {
        check(IsInGameThread());
        const TCHAR* CmdName = TEXT("pie_set_player_rotation");
        if (GEditor == nullptr || GEditor->PlayWorld == nullptr)
            return JsonError(CmdName, TEXT("pie_not_running"));

        APlayerController* PC = UGameplayStatics::GetPlayerController(GEditor->PlayWorld, PlayerIndex);
        if (PC == nullptr)
            return JsonError(CmdName, TEXT("no_player_controller"),
                FString::Printf(TEXT("player_index=%d"), PlayerIndex));

        const FRotator NewRotation(Pitch, Yaw, Roll);
        PC->SetControlRotation(NewRotation);

        const FRotator Applied = PC->GetControlRotation();
        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"pie_set_player_rotation\",\"player_index\":%d,")
            TEXT("\"requested\":[%f,%f,%f],\"applied\":[%f,%f,%f]}\n"),
            PlayerIndex,
            Pitch, Yaw, Roll,
            Applied.Pitch, Applied.Yaw, Applied.Roll);
    }

    // ===== v8.1 — log capture read/clear (no game-thread needed; capture is thread-safe) =====

    FString ReadLogCaptureSync(int32 MaxLines, const FString& CategoryFilter, const FString& VerbosityFilter, const FString& Substring)
    {
        const TCHAR* CmdName = TEXT("read_log_capture");
        if (GBlueprintMCPLogCapture == nullptr)
            return JsonError(CmdName, TEXT("log_capture_not_installed"), TEXT(""));

        // Snapshot copies under lock; safe to filter/serialize without holding mutex.
        TArray<FString> All = GBlueprintMCPLogCapture->Snapshot(/*MaxLines=*/ 0);  // 0 = all

        // v8.0.3 BUG-A fix: previously we wrapped CategoryFilter in `[%s]` and looked for
        // that as a substring of the line. That made the filter act as prefix-match
        // ("BlueprintMCP" wouldn't match "[LogBlueprintMCP_TCP]" because of the trailing
        // bracket). Now we parse the line's bracketed prefix and substring-match the
        // user's filter against the category/verbosity tokens individually, matching the
        // documented "contains, case-insensitive" behavior.
        const bool bHasCat   = !CategoryFilter.IsEmpty();
        const bool bHasVerb  = !VerbosityFilter.IsEmpty();
        const bool bHasSub   = !Substring.IsEmpty();

        auto ExtractBracketToken = [](const FString& Line, int32 NthBracket /*0-based*/) -> FString {
            int32 SearchStart = 0;
            for (int32 i = 0; i <= NthBracket; ++i)
            {
                const int32 Open = Line.Find(TEXT("["), ESearchCase::CaseSensitive, ESearchDir::FromStart, SearchStart);
                if (Open == INDEX_NONE) return FString();
                const int32 Close = Line.Find(TEXT("]"), ESearchCase::CaseSensitive, ESearchDir::FromStart, Open + 1);
                if (Close == INDEX_NONE) return FString();
                if (i == NthBracket)
                {
                    return Line.Mid(Open + 1, Close - Open - 1);
                }
                SearchStart = Close + 1;
            }
            return FString();
        };

        TArray<FString> Filtered;
        Filtered.Reserve(All.Num());
        for (const FString& Line : All)
        {
            if (bHasCat)
            {
                const FString LineCat = ExtractBracketToken(Line, /*Nth=*/ 0);
                if (!LineCat.Contains(CategoryFilter, ESearchCase::IgnoreCase))
                    continue;
            }
            if (bHasVerb)
            {
                const FString LineVerb = ExtractBracketToken(Line, /*Nth=*/ 1);
                if (!LineVerb.Contains(VerbosityFilter, ESearchCase::IgnoreCase))
                    continue;
            }
            if (bHasSub && !Line.Contains(Substring, ESearchCase::IgnoreCase))
                continue;
            Filtered.Add(Line);
        }

        // Tail-trim to MaxLines (if > 0)
        if (MaxLines > 0 && Filtered.Num() > MaxLines)
        {
            const int32 Drop = Filtered.Num() - MaxLines;
            Filtered.RemoveAt(0, Drop, EAllowShrinking::No);
        }

        // Build JSON via TJsonWriter (auto-escapes strings — important; log lines have arbitrary content)
        FString OutJson;
        TSharedRef<TJsonWriter<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>> Writer =
            TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&OutJson);
        Writer->WriteObjectStart();
        Writer->WriteValue(TEXT("ok"), true);
        Writer->WriteValue(TEXT("command"), TEXT("read_log_capture"));
        Writer->WriteValue(TEXT("total_captured"), All.Num());
        Writer->WriteValue(TEXT("returned"), Filtered.Num());
        Writer->WriteArrayStart(TEXT("lines"));
        for (const FString& Line : Filtered)
        {
            Writer->WriteValue(Line);
        }
        Writer->WriteArrayEnd();
        Writer->WriteObjectEnd();
        Writer->Close();
        return OutJson + TEXT("\n");
    }

    FString ClearLogCaptureSync()
    {
        const TCHAR* CmdName = TEXT("clear_log_capture");
        if (GBlueprintMCPLogCapture == nullptr)
            return JsonError(CmdName, TEXT("log_capture_not_installed"), TEXT(""));
        GBlueprintMCPLogCapture->Clear();
        return FString(TEXT("{\"ok\":true,\"command\":\"clear_log_capture\"}\n"));
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
    // v8.0.1 OPEN-2: promote from Verbose to Log so FOutputDevice (and therefore
    // read_log_capture) sees every MCP request/response. Truncate at 800 chars to
    // keep the buffer readable when large get_blueprint snapshots are flowing.
    UE_LOG(LogBlueprintMCP_TCP, Log, TEXT("MCP recv: %s%s"),
        *(JsonLine.Len() > 800 ? JsonLine.Left(800) : JsonLine),
        JsonLine.Len() > 800 ? TEXT("...[truncated]") : TEXT(""));

    const FString Response = DispatchCommand(JsonLine);
    const FTCHARToUTF8 ResponseUtf8(*Response);
    int32 BytesSent = 0;
    ClientSocket->Send(reinterpret_cast<const uint8*>(ResponseUtf8.Get()), ResponseUtf8.Length(), BytesSent);
    UE_LOG(LogBlueprintMCP_TCP, Log, TEXT("MCP send: %s%s"),
        *(Response.Len() > 800 ? Response.Left(800) : Response),
        Response.Len() > 800 ? TEXT("...[truncated]") : TEXT(""));
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

    // --- ping (Spike A1 + v8.0.2 plugin_version + build_date) ---
    if (Command.Equals(TEXT("ping"), ESearchCase::IgnoreCase))
    {
        const FString Timestamp = FDateTime::UtcNow().ToIso8601();
        // __DATE__ and __TIME__ resolve at compile time. ANSI string → TCHAR via TEXT() wrap.
        return FString::Printf(
            TEXT("{\"ok\":true,\"command\":\"ping\",\"version\":\"0.0.1\",\"plugin_version\":\"9.10.0\",\"build_date\":\"%s %s\",\"timestamp\":\"%s\"}\n"),
            TEXT(__DATE__), TEXT(__TIME__),
            *Timestamp);
    }

    // --- v9.1.0 Discovery tools ---
    // ALL go through AsyncTask(GameThread, ...) — IAssetRegistry asserts game-thread
    // because its filtering globals aren't thread-safe (crash discovered in initial
    // v9.1.0 testing).
    if (Command.Equals(TEXT("list_assets"), ESearchCase::IgnoreCase))
    {
        FString Folder, AssetClass;
        JsonObject->TryGetStringField(TEXT("folder"), Folder);
        JsonObject->TryGetStringField(TEXT("asset_class"), AssetClass);
        bool bRecursive = true;
        JsonObject->TryGetBoolField(TEXT("recursive"), bRecursive);
        int32 MaxResults = 500;
        JsonObject->TryGetNumberField(TEXT("max_results"), MaxResults);

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Folder, AssetClass, bRecursive, MaxResults]() mutable
            {
                Promise.SetValue(ListAssetsCore(TEXT("list_assets"), Folder, AssetClass, bRecursive, MaxResults));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("list_assets"), TEXT("game_thread_timeout"));
        return Future.Get();
    }
    if (Command.Equals(TEXT("list_skeletons"), ESearchCase::IgnoreCase))
    {
        FString Folder; JsonObject->TryGetStringField(TEXT("folder"), Folder);
        int32 MaxResults = 100; JsonObject->TryGetNumberField(TEXT("max_results"), MaxResults);

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Folder, MaxResults]() mutable
            {
                Promise.SetValue(ListAssetsCore(TEXT("list_skeletons"), Folder, TEXT("Skeleton"), true, MaxResults));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("list_skeletons"), TEXT("game_thread_timeout"));
        return Future.Get();
    }
    if (Command.Equals(TEXT("list_meshes"), ESearchCase::IgnoreCase))
    {
        FString Folder; JsonObject->TryGetStringField(TEXT("folder"), Folder);
        int32 MaxResults = 200; JsonObject->TryGetNumberField(TEXT("max_results"), MaxResults);

        // Single game-thread hop that runs both class queries + merges, so we don't
        // double the latency by marshaling twice.
        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Folder, MaxResults]() mutable
            {
                const FString StaticJson = ListAssetsCore(TEXT("list_meshes"), Folder, TEXT("StaticMesh"), true, MaxResults);
                const FString SkelJson   = ListAssetsCore(TEXT("list_meshes"), Folder, TEXT("SkeletalMesh"), true, MaxResults);
                TSharedPtr<FJsonObject> StaticObj, SkelObj;
                FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(StaticJson), StaticObj);
                FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(SkelJson), SkelObj);
                const TArray<TSharedPtr<FJsonValue>>* StaticArr = nullptr;
                const TArray<TSharedPtr<FJsonValue>>* SkelArr = nullptr;
                if (StaticObj.IsValid()) StaticObj->TryGetArrayField(TEXT("assets"), StaticArr);
                if (SkelObj.IsValid())   SkelObj->TryGetArrayField(TEXT("assets"), SkelArr);

                FString OutJson;
                TSharedRef<TJsonWriter<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>> W =
                    TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&OutJson);
                W->WriteObjectStart();
                W->WriteValue(TEXT("ok"), true);
                W->WriteValue(TEXT("command"), TEXT("list_meshes"));
                W->WriteValue(TEXT("folder"), Folder.IsEmpty() ? FString(TEXT("/Game")) : Folder);
                const int32 StaticCount = StaticArr ? StaticArr->Num() : 0;
                const int32 SkelCount   = SkelArr   ? SkelArr->Num()   : 0;
                W->WriteValue(TEXT("static_count"), StaticCount);
                W->WriteValue(TEXT("skeletal_count"), SkelCount);
                W->WriteValue(TEXT("count"), StaticCount + SkelCount);
                W->WriteArrayStart(TEXT("assets"));
                if (StaticArr) for (const auto& V : *StaticArr) FJsonSerializer::Serialize(V.ToSharedRef(), TEXT(""), W, false);
                if (SkelArr)   for (const auto& V : *SkelArr)   FJsonSerializer::Serialize(V.ToSharedRef(), TEXT(""), W, false);
                W->WriteArrayEnd();
                W->WriteObjectEnd();
                W->Close();
                Promise.SetValue(OutJson + TEXT("\n"));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("list_meshes"), TEXT("game_thread_timeout"));
        return Future.Get();
    }
    if (Command.Equals(TEXT("list_blueprints"), ESearchCase::IgnoreCase))
    {
        FString Folder; JsonObject->TryGetStringField(TEXT("folder"), Folder);
        int32 MaxResults = 200; JsonObject->TryGetNumberField(TEXT("max_results"), MaxResults);

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Folder, MaxResults]() mutable
            {
                Promise.SetValue(ListAssetsCore(TEXT("list_blueprints"), Folder, TEXT("Blueprint"), true, MaxResults));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("list_blueprints"), TEXT("game_thread_timeout"));
        return Future.Get();
    }
    if (Command.Equals(TEXT("list_classes"), ESearchCase::IgnoreCase))
    {
        FString ParentClass, NameContains;
        JsonObject->TryGetStringField(TEXT("parent_class"), ParentClass);
        JsonObject->TryGetStringField(TEXT("name_contains"), NameContains);
        bool bNativeOnly = false;
        JsonObject->TryGetBoolField(TEXT("native_only"), bNativeOnly);
        int32 MaxResults = 200;
        JsonObject->TryGetNumberField(TEXT("max_results"), MaxResults);

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), ParentClass, bNativeOnly, NameContains, MaxResults]() mutable
            {
                Promise.SetValue(ListClassesCore(ParentClass, bNativeOnly, NameContains, MaxResults));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("list_classes"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- create_anim_blueprint (v9.0.0) ---
    if (Command.Equals(TEXT("create_anim_blueprint"), ESearchCase::IgnoreCase))
    {
        FString Name, SkeletonPath, Path;
        if (!JsonObject->TryGetStringField(TEXT("name"), Name) || Name.IsEmpty())
            return JsonError(TEXT("create_anim_blueprint"), TEXT("missing_field"), TEXT("name"));
        if (!JsonObject->TryGetStringField(TEXT("skeleton"), SkeletonPath) || SkeletonPath.IsEmpty())
            return JsonError(TEXT("create_anim_blueprint"), TEXT("missing_field"), TEXT("skeleton"));
        if (!JsonObject->TryGetStringField(TEXT("path"), Path) || Path.IsEmpty())
            Path = TEXT("/Game/Blueprints");

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Name, SkeletonPath, Path]() mutable
            {
                Promise.SetValue(CreateAnimBlueprintOnGameThread(Name, SkeletonPath, Path));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("create_anim_blueprint"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- v9.2.0 AnimGraph state-machine tools ---
    // All four go through AsyncTask(GameThread, ...) — AnimGraph mutations touch
    // UObject graphs and must run on the game thread.
    if (Command.Equals(TEXT("add_anim_state_machine"), ESearchCase::IgnoreCase))
    {
        FString BlueprintPath, StateMachineName;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), BlueprintPath) || BlueprintPath.IsEmpty())
            return JsonError(TEXT("add_anim_state_machine"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("name"), StateMachineName) || StateMachineName.IsEmpty())
            return JsonError(TEXT("add_anim_state_machine"), TEXT("missing_field"), TEXT("name"));
        int32 PosX = 0, PosY = 0;
        JsonObject->TryGetNumberField(TEXT("pos_x"), PosX);
        JsonObject->TryGetNumberField(TEXT("pos_y"), PosY);

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), BlueprintPath, StateMachineName, PosX, PosY]() mutable
            {
                Promise.SetValue(AddAnimStateMachineOnGameThread(BlueprintPath, StateMachineName, PosX, PosY));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("add_anim_state_machine"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    if (Command.Equals(TEXT("add_anim_state"), ESearchCase::IgnoreCase))
    {
        FString BlueprintPath, StateMachineName, StateName;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), BlueprintPath) || BlueprintPath.IsEmpty())
            return JsonError(TEXT("add_anim_state"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("state_machine"), StateMachineName) || StateMachineName.IsEmpty())
            return JsonError(TEXT("add_anim_state"), TEXT("missing_field"), TEXT("state_machine"));
        if (!JsonObject->TryGetStringField(TEXT("name"), StateName) || StateName.IsEmpty())
            return JsonError(TEXT("add_anim_state"), TEXT("missing_field"), TEXT("name"));
        int32 PosX = 0, PosY = 0;
        JsonObject->TryGetNumberField(TEXT("pos_x"), PosX);
        JsonObject->TryGetNumberField(TEXT("pos_y"), PosY);

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), BlueprintPath, StateMachineName, StateName, PosX, PosY]() mutable
            {
                Promise.SetValue(AddAnimStateOnGameThread(BlueprintPath, StateMachineName, StateName, PosX, PosY));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("add_anim_state"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    if (Command.Equals(TEXT("add_anim_transition"), ESearchCase::IgnoreCase))
    {
        FString BlueprintPath, StateMachineName, FromStateName, ToStateName;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), BlueprintPath) || BlueprintPath.IsEmpty())
            return JsonError(TEXT("add_anim_transition"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("state_machine"), StateMachineName) || StateMachineName.IsEmpty())
            return JsonError(TEXT("add_anim_transition"), TEXT("missing_field"), TEXT("state_machine"));
        if (!JsonObject->TryGetStringField(TEXT("from_state"), FromStateName) || FromStateName.IsEmpty())
            return JsonError(TEXT("add_anim_transition"), TEXT("missing_field"), TEXT("from_state"));
        if (!JsonObject->TryGetStringField(TEXT("to_state"), ToStateName) || ToStateName.IsEmpty())
            return JsonError(TEXT("add_anim_transition"), TEXT("missing_field"), TEXT("to_state"));

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), BlueprintPath, StateMachineName, FromStateName, ToStateName]() mutable
            {
                Promise.SetValue(AddAnimTransitionOnGameThread(BlueprintPath, StateMachineName, FromStateName, ToStateName));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("add_anim_transition"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    if (Command.Equals(TEXT("set_anim_state_pose"), ESearchCase::IgnoreCase))
    {
        FString BlueprintPath, StateMachineName, StateName, SequencePath;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), BlueprintPath) || BlueprintPath.IsEmpty())
            return JsonError(TEXT("set_anim_state_pose"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("state_machine"), StateMachineName) || StateMachineName.IsEmpty())
            return JsonError(TEXT("set_anim_state_pose"), TEXT("missing_field"), TEXT("state_machine"));
        if (!JsonObject->TryGetStringField(TEXT("state"), StateName) || StateName.IsEmpty())
            return JsonError(TEXT("set_anim_state_pose"), TEXT("missing_field"), TEXT("state"));
        if (!JsonObject->TryGetStringField(TEXT("sequence"), SequencePath) || SequencePath.IsEmpty())
            return JsonError(TEXT("set_anim_state_pose"), TEXT("missing_field"), TEXT("sequence"));

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), BlueprintPath, StateMachineName, StateName, SequencePath]() mutable
            {
                Promise.SetValue(SetAnimStatePoseOnGameThread(BlueprintPath, StateMachineName, StateName, SequencePath));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("set_anim_state_pose"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- v9.3.0 Niagara door-opener ---
    if (Command.Equals(TEXT("create_niagara_system"), ESearchCase::IgnoreCase))
    {
        FString Name, Path;
        if (!JsonObject->TryGetStringField(TEXT("name"), Name) || Name.IsEmpty())
            return JsonError(TEXT("create_niagara_system"), TEXT("missing_field"), TEXT("name"));
        if (!JsonObject->TryGetStringField(TEXT("path"), Path) || Path.IsEmpty())
            Path = TEXT("/Game/VFX");

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Name, Path]() mutable
            {
                Promise.SetValue(CreateNiagaraSystemOnGameThread(Name, Path));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("create_niagara_system"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- v9.4.0 UMG door-opener ---
    if (Command.Equals(TEXT("create_widget_blueprint"), ESearchCase::IgnoreCase))
    {
        FString Name, ParentClassName, Path;
        if (!JsonObject->TryGetStringField(TEXT("name"), Name) || Name.IsEmpty())
            return JsonError(TEXT("create_widget_blueprint"), TEXT("missing_field"), TEXT("name"));
        JsonObject->TryGetStringField(TEXT("parent_class"), ParentClassName);   // optional
        if (!JsonObject->TryGetStringField(TEXT("path"), Path) || Path.IsEmpty())
            Path = TEXT("/Game/UI");

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Name, ParentClassName, Path]() mutable
            {
                Promise.SetValue(CreateWidgetBlueprintOnGameThread(Name, ParentClassName, Path));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("create_widget_blueprint"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- v9.4.0 save_all ---
    // Silently save every dirty package. Use this before any UE editor kill
    // to skip the "save changes?" dialog on next launch.
    if (Command.Equals(TEXT("save_all"), ESearchCase::IgnoreCase))
    {
        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise)]() mutable
            {
                Promise.SetValue(SaveAllOnGameThread());
            });
        // save_all can take longer than 10s on large projects — give it 30s.
        if (!Future.WaitFor(FTimespan::FromSeconds(30)))
            return JsonError(TEXT("save_all"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- v9.6.0 shutdown_editor ---
    // Clean shutdown signal — works in BOTH headless (commandlet) and GUI modes.
    //
    // Headless (BlueprintMCPRunCommandlet): flips bShouldExit; commandlet's
    // sleep loop sees it on next tick (~250ms) and returns 0.
    //
    // GUI: schedules FPlatformMisc::RequestExit(false) on the game thread,
    // which is the same exit path used by File → Exit. Any dirty packages
    // will prompt unless caller ran save_all first.
    //
    // Returns immediately — doesn't wait for the shutdown to actually complete.
    if (Command.Equals(TEXT("shutdown_editor"), ESearchCase::IgnoreCase))
    {
        UBlueprintMCPRunCommandlet::bShouldExit = true;
        AsyncTask(ENamedThreads::GameThread, [](){
            FPlatformMisc::RequestExit(/*Force*/ false);
        });
        return TEXT("{\"ok\":true,\"command\":\"shutdown_editor\",\"requested\":true}\n");
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

    // --- add_custom_event (Spike B8 + v7.5 params) ---
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

        // v7.5: optional "params" array of {name, type} objects
        TArray<FString> ParamNames, ParamTypes;
        const TArray<TSharedPtr<FJsonValue>>* ParamsArray = nullptr;
        if (JsonObject->TryGetArrayField(TEXT("params"), ParamsArray) && ParamsArray != nullptr)
        {
            for (const TSharedPtr<FJsonValue>& Item : *ParamsArray)
            {
                const TSharedPtr<FJsonObject>* ParamObjPtr = nullptr;
                if (Item.IsValid() && Item->TryGetObject(ParamObjPtr) && ParamObjPtr != nullptr)
                {
                    FString PName, PType;
                    (*ParamObjPtr)->TryGetStringField(TEXT("name"), PName);
                    (*ParamObjPtr)->TryGetStringField(TEXT("type"), PType);
                    if (!PName.IsEmpty() && !PType.IsEmpty())
                    {
                        ParamNames.Add(PName);
                        ParamTypes.Add(PType);
                    }
                }
            }
        }

        FString GraphName;
        JsonObject->TryGetStringField(TEXT("graph_name"), GraphName);   // v7.7.1

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, EventName, AnchorName, PosX, PosY, ParamNames, ParamTypes, GraphName]() mutable
            {
                Promise.SetValue(AddCustomEventOnGameThread(Blueprint, EventName, AnchorName, PosX, PosY, ParamNames, ParamTypes, GraphName));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("add_custom_event"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- add_variable (Spike B9 + v9.8.0 instance_editable) ---
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
        bool bInstanceEditable = false;
        JsonObject->TryGetBoolField(TEXT("instance_editable"), bInstanceEditable);   // v9.8.0

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, Name, VarType, DefaultValue, bInstanceEditable]() mutable
            {
                Promise.SetValue(AddVariableOnGameThread(Blueprint, Name, VarType, DefaultValue, bInstanceEditable));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("add_variable"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- v9.8.0 set_variable_flags ---
    // Each flag is tri-state: omitted = leave unchanged. None / null in JSON
    // is treated as omitted. Pass a real bool to set.
    if (Command.Equals(TEXT("set_variable_flags"), ESearchCase::IgnoreCase))
    {
        FString Blueprint, Name;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(TEXT("set_variable_flags"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("name"), Name) || Name.IsEmpty())
            return JsonError(TEXT("set_variable_flags"), TEXT("missing_field"), TEXT("name"));

        bool bInstanceEditable = false;
        const bool bHasInstanceEditable = JsonObject->TryGetBoolField(TEXT("instance_editable"), bInstanceEditable);
        bool bReadOnly = false;
        const bool bHasReadOnly = JsonObject->TryGetBoolField(TEXT("blueprint_read_only"), bReadOnly);
        bool bExposeOnSpawn = false;
        const bool bHasExposeOnSpawn = JsonObject->TryGetBoolField(TEXT("expose_on_spawn"), bExposeOnSpawn);

        if (!bHasInstanceEditable && !bHasReadOnly && !bHasExposeOnSpawn)
            return JsonError(TEXT("set_variable_flags"), TEXT("no_flag_specified"),
                TEXT("Provide at least one of: instance_editable, blueprint_read_only, expose_on_spawn"));

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, Name,
             bHasInstanceEditable, bInstanceEditable,
             bHasReadOnly, bReadOnly,
             bHasExposeOnSpawn, bExposeOnSpawn]() mutable
            {
                Promise.SetValue(SetVariableFlagsOnGameThread(
                    Blueprint, Name,
                    bHasInstanceEditable, bInstanceEditable,
                    bHasReadOnly, bReadOnly,
                    bHasExposeOnSpawn, bExposeOnSpawn));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("set_variable_flags"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- v9.8.0 delete_variable ---
    if (Command.Equals(TEXT("delete_variable"), ESearchCase::IgnoreCase))
    {
        FString Blueprint, Name;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(TEXT("delete_variable"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("name"), Name) || Name.IsEmpty())
            return JsonError(TEXT("delete_variable"), TEXT("missing_field"), TEXT("name"));

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, Name]() mutable
            {
                Promise.SetValue(DeleteVariableOnGameThread(Blueprint, Name));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("delete_variable"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- v9.8.0 delete_blueprint ---
    if (Command.Equals(TEXT("delete_blueprint"), ESearchCase::IgnoreCase))
    {
        FString Path;
        if (!JsonObject->TryGetStringField(TEXT("path"), Path) || Path.IsEmpty())
            return JsonError(TEXT("delete_blueprint"), TEXT("missing_field"), TEXT("path"));

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Path]() mutable
            {
                Promise.SetValue(DeleteBlueprintOnGameThread(Path));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("delete_blueprint"), TEXT("game_thread_timeout"));
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

        FString GraphName;
        JsonObject->TryGetStringField(TEXT("graph_name"), GraphName);

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, VariableName, AnchorName, PosX, PosY, bIsSet, GraphName]() mutable
            {
                Promise.SetValue(AddVariableRefOnGameThread(Blueprint, VariableName, AnchorName, PosX, PosY, bIsSet, GraphName));
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

    // --- call_blueprint_function (v5 + v6 target_pin extension) ---
    if (Command.Equals(TEXT("call_blueprint_function"), ESearchCase::IgnoreCase))
    {
        FString Blueprint, TargetClass, FunctionName, AnchorName, TargetPin;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(TEXT("call_blueprint_function"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("target_class"), TargetClass) || TargetClass.IsEmpty())
            return JsonError(TEXT("call_blueprint_function"), TEXT("missing_field"), TEXT("target_class"));
        if (!JsonObject->TryGetStringField(TEXT("function_name"), FunctionName) || FunctionName.IsEmpty())
            return JsonError(TEXT("call_blueprint_function"), TEXT("missing_field"), TEXT("function_name"));
        if (!JsonObject->TryGetStringField(TEXT("anchor_name"), AnchorName) || AnchorName.IsEmpty())
            return JsonError(TEXT("call_blueprint_function"), TEXT("missing_field"), TEXT("anchor_name"));
        // optional v6: target_pin to auto-wire self
        JsonObject->TryGetStringField(TEXT("target_pin"), TargetPin);
        int32 PosX = 0, PosY = 0;
        JsonObject->TryGetNumberField(TEXT("position_x"), PosX);
        JsonObject->TryGetNumberField(TEXT("position_y"), PosY);

        FString GraphName;
        JsonObject->TryGetStringField(TEXT("graph_name"), GraphName);   // v7.7.1

        TPromise<FString> Promise; TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, TargetClass, FunctionName, AnchorName, PosX, PosY, TargetPin, GraphName]() mutable
            { Promise.SetValue(CallBlueprintFunctionOnGameThread(Blueprint, TargetClass, FunctionName, AnchorName, PosX, PosY, TargetPin, GraphName)); });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("call_blueprint_function"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- wire_imc_subscribe (v6) ---
    if (Command.Equals(TEXT("wire_imc_subscribe"), ESearchCase::IgnoreCase))
    {
        FString Blueprint, IMCPath, AnchorPrefix;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(TEXT("wire_imc_subscribe"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("imc_path"), IMCPath) || IMCPath.IsEmpty())
            return JsonError(TEXT("wire_imc_subscribe"), TEXT("missing_field"), TEXT("imc_path"));
        if (!JsonObject->TryGetStringField(TEXT("anchor_prefix"), AnchorPrefix) || AnchorPrefix.IsEmpty())
            AnchorPrefix = TEXT("imc_sub");
        int32 Priority = 0;
        JsonObject->TryGetNumberField(TEXT("priority"), Priority);

        TPromise<FString> Promise; TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, IMCPath, Priority, AnchorPrefix]() mutable
            { Promise.SetValue(WireImcSubscribeOnGameThread(Blueprint, IMCPath, Priority, AnchorPrefix)); });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("wire_imc_subscribe"), TEXT("game_thread_timeout"));
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

    // --- add_macro (v4 + v7.7.1 graph_name) ---
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
        FString GraphName;
        JsonObject->TryGetStringField(TEXT("graph_name"), GraphName);

        TPromise<FString> Promise; TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, MacroType, AnchorName, PosX, PosY, GraphName]() mutable
            { Promise.SetValue(AddMacroOnGameThread(Blueprint, MacroType, AnchorName, PosX, PosY, GraphName)); });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("add_macro"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- add_self_reference (v4 + v7.7.1 graph_name) ---
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
        FString GraphName;
        JsonObject->TryGetStringField(TEXT("graph_name"), GraphName);

        TPromise<FString> Promise; TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, AnchorName, PosX, PosY, GraphName]() mutable
            { Promise.SetValue(AddSelfReferenceOnGameThread(Blueprint, AnchorName, PosX, PosY, GraphName)); });
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

    // --- delete_node (v4 + v7.7.1 graph_name) ---
    if (Command.Equals(TEXT("delete_node"), ESearchCase::IgnoreCase))
    {
        FString Blueprint, AnchorName;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(TEXT("delete_node"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("anchor_name"), AnchorName) || AnchorName.IsEmpty())
            return JsonError(TEXT("delete_node"), TEXT("missing_field"), TEXT("anchor_name"));
        FString GraphName;
        JsonObject->TryGetStringField(TEXT("graph_name"), GraphName);

        TPromise<FString> Promise; TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, AnchorName, GraphName]() mutable
            { Promise.SetValue(DeleteNodeOnGameThread(Blueprint, AnchorName, GraphName)); });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("delete_node"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- disconnect_pins (v4 + v7.7.1 graph_name) ---
    if (Command.Equals(TEXT("disconnect_pins"), ESearchCase::IgnoreCase))
    {
        FString Blueprint, FromPin, ToPin;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(TEXT("disconnect_pins"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("from_pin"), FromPin) || FromPin.IsEmpty())
            return JsonError(TEXT("disconnect_pins"), TEXT("missing_field"), TEXT("from_pin"));
        if (!JsonObject->TryGetStringField(TEXT("to_pin"), ToPin) || ToPin.IsEmpty())
            return JsonError(TEXT("disconnect_pins"), TEXT("missing_field"), TEXT("to_pin"));
        FString GraphName;
        JsonObject->TryGetStringField(TEXT("graph_name"), GraphName);

        TPromise<FString> Promise; TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, FromPin, ToPin, GraphName]() mutable
            { Promise.SetValue(DisconnectPinsOnGameThread(Blueprint, FromPin, ToPin, GraphName)); });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("disconnect_pins"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- add_branch (v3 + v7.7 graph_name) ---
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

        FString GraphName;
        JsonObject->TryGetStringField(TEXT("graph_name"), GraphName);

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, AnchorName, PosX, PosY, GraphName]() mutable
            {
                Promise.SetValue(AddBranchOnGameThread(Blueprint, AnchorName, PosX, PosY, GraphName));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("add_branch"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- add_cast (v3 + v7.7 graph_name) ---
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

        FString GraphName;
        JsonObject->TryGetStringField(TEXT("graph_name"), GraphName);

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, TargetClass, AnchorName, PosX, PosY, GraphName]() mutable
            {
                Promise.SetValue(AddCastOnGameThread(Blueprint, TargetClass, AnchorName, PosX, PosY, GraphName));
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

    // --- v9.7.0 list_level_actors ---
    if (Command.Equals(TEXT("list_level_actors"), ESearchCase::IgnoreCase))
    {
        FString ClassFilter, NameContains;
        JsonObject->TryGetStringField(TEXT("class_filter"), ClassFilter);
        JsonObject->TryGetStringField(TEXT("name_contains"), NameContains);
        int32 MaxResults = 500;
        JsonObject->TryGetNumberField(TEXT("max_results"), MaxResults);

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), ClassFilter, NameContains, MaxResults]() mutable
            {
                Promise.SetValue(ListLevelActorsOnGameThread(ClassFilter, NameContains, MaxResults));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("list_level_actors"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- v9.7.0 get_actor_transform ---
    if (Command.Equals(TEXT("get_actor_transform"), ESearchCase::IgnoreCase))
    {
        FString ActorName;
        if (!JsonObject->TryGetStringField(TEXT("actor"), ActorName) || ActorName.IsEmpty())
            return JsonError(TEXT("get_actor_transform"), TEXT("missing_field"), TEXT("actor"));

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), ActorName]() mutable
            {
                Promise.SetValue(GetActorTransformOnGameThread(ActorName));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("get_actor_transform"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- v9.7.0 set_actor_transform ---
    // Any of location/rotation/scale may be omitted — only fields that were
    // supplied in the JSON get applied. bSetLoc/bSetRot/bSetScale tracks which.
    if (Command.Equals(TEXT("set_actor_transform"), ESearchCase::IgnoreCase))
    {
        FString ActorName;
        if (!JsonObject->TryGetStringField(TEXT("actor"), ActorName) || ActorName.IsEmpty())
            return JsonError(TEXT("set_actor_transform"), TEXT("missing_field"), TEXT("actor"));

        FVector Loc = FVector::ZeroVector;
        FRotator Rot = FRotator::ZeroRotator;
        FVector Scale = FVector::OneVector;
        bool bSetLoc = false, bSetRot = false, bSetScale = false;

        const TArray<TSharedPtr<FJsonValue>>* LocArr = nullptr;
        if (JsonObject->TryGetArrayField(TEXT("location"), LocArr) && LocArr->Num() >= 3)
        {
            Loc.X = (*LocArr)[0]->AsNumber();
            Loc.Y = (*LocArr)[1]->AsNumber();
            Loc.Z = (*LocArr)[2]->AsNumber();
            bSetLoc = true;
        }
        const TArray<TSharedPtr<FJsonValue>>* RotArr = nullptr;
        if (JsonObject->TryGetArrayField(TEXT("rotation"), RotArr) && RotArr->Num() >= 3)
        {
            Rot.Pitch = (*RotArr)[0]->AsNumber();
            Rot.Yaw   = (*RotArr)[1]->AsNumber();
            Rot.Roll  = (*RotArr)[2]->AsNumber();
            bSetRot = true;
        }
        const TArray<TSharedPtr<FJsonValue>>* ScaleArr = nullptr;
        if (JsonObject->TryGetArrayField(TEXT("scale"), ScaleArr) && ScaleArr->Num() >= 3)
        {
            Scale.X = (*ScaleArr)[0]->AsNumber();
            Scale.Y = (*ScaleArr)[1]->AsNumber();
            Scale.Z = (*ScaleArr)[2]->AsNumber();
            bSetScale = true;
        }

        if (!bSetLoc && !bSetRot && !bSetScale)
            return JsonError(TEXT("set_actor_transform"), TEXT("no_change_specified"),
                TEXT("Provide at least one of: location, rotation, scale"));

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), ActorName, Loc, Rot, Scale, bSetLoc, bSetRot, bSetScale]() mutable
            {
                Promise.SetValue(SetActorTransformOnGameThread(ActorName, Loc, Rot, Scale, bSetLoc, bSetRot, bSetScale));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("set_actor_transform"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- v9.7.0 set_actor_property ---
    if (Command.Equals(TEXT("set_actor_property"), ESearchCase::IgnoreCase))
    {
        FString ActorName, PropertyPath, Value;
        if (!JsonObject->TryGetStringField(TEXT("actor"), ActorName) || ActorName.IsEmpty())
            return JsonError(TEXT("set_actor_property"), TEXT("missing_field"), TEXT("actor"));
        if (!JsonObject->TryGetStringField(TEXT("property"), PropertyPath) || PropertyPath.IsEmpty())
            return JsonError(TEXT("set_actor_property"), TEXT("missing_field"), TEXT("property"));
        JsonObject->TryGetStringField(TEXT("value"), Value);   // value may legitimately be empty (== clear)

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), ActorName, PropertyPath, Value]() mutable
            {
                Promise.SetValue(SetActorPropertyOnGameThread(ActorName, PropertyPath, Value));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("set_actor_property"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- v9.7.0 delete_actor ---
    if (Command.Equals(TEXT("delete_actor"), ESearchCase::IgnoreCase))
    {
        FString ActorName;
        if (!JsonObject->TryGetStringField(TEXT("actor"), ActorName) || ActorName.IsEmpty())
            return JsonError(TEXT("delete_actor"), TEXT("missing_field"), TEXT("actor"));

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), ActorName]() mutable
            {
                Promise.SetValue(DeleteActorOnGameThread(ActorName));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("delete_actor"), TEXT("game_thread_timeout"));
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

    // --- connect_pins (Spike B4 + v7.7 graph_name) ---
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

        FString GraphName;
        JsonObject->TryGetStringField(TEXT("graph_name"), GraphName);

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();

        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, FromPin, ToPin, GraphName]() mutable
            {
                FString Result = ConnectPinsOnGameThread(Blueprint, FromPin, ToPin, GraphName);
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

    // --- set_pin_default (Spike B3 + v7.7 graph_name) ---
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

        FString GraphName;
        JsonObject->TryGetStringField(TEXT("graph_name"), GraphName);

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();

        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, PinRef, Value, GraphName]() mutable
            {
                FString Result = SetPinDefaultOnGameThread(Blueprint, PinRef, Value, GraphName);
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

    // --- add_node (Spike B2 + v7.7 graph_name) ---
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

        FString GraphName;
        JsonObject->TryGetStringField(TEXT("graph_name"), GraphName);   // v7.7: optional

        // Marshal to game thread (same TPromise/TFuture pattern as create_blueprint)
        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();

        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, NodeType, AnchorName, PosX, PosY, GraphName]() mutable
            {
                FString Result = AddNodeOnGameThread(Blueprint, NodeType, AnchorName, PosX, PosY, GraphName);
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

    // --- set_component_property (v7.1) ---
    if (Command.Equals(TEXT("set_component_property"), ESearchCase::IgnoreCase))
    {
        FString Blueprint, ComponentName, PropertyName, Value;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(TEXT("set_component_property"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("component_name"), ComponentName) || ComponentName.IsEmpty())
            return JsonError(TEXT("set_component_property"), TEXT("missing_field"), TEXT("component_name"));
        if (!JsonObject->TryGetStringField(TEXT("property_name"), PropertyName) || PropertyName.IsEmpty())
            return JsonError(TEXT("set_component_property"), TEXT("missing_field"), TEXT("property_name"));
        // value field MUST be present, but empty string is allowed (clears object/class refs)
        if (!JsonObject->HasField(TEXT("value")))
            return JsonError(TEXT("set_component_property"), TEXT("missing_field"), TEXT("value"));
        JsonObject->TryGetStringField(TEXT("value"), Value);

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, ComponentName, PropertyName, Value]() mutable
            {
                Promise.SetValue(SetComponentPropertyOnGameThread(Blueprint, ComponentName, PropertyName, Value));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
        {
            UE_LOG(LogBlueprintMCP_TCP, Error, TEXT("set_component_property timed out after %ds"), kGameThreadTimeoutSeconds);
            return JsonError(TEXT("set_component_property"), TEXT("game_thread_timeout"));
        }
        return Future.Get();
    }

    // --- add_switch (v7.2) ---
    if (Command.Equals(TEXT("add_switch"), ESearchCase::IgnoreCase))
    {
        FString Blueprint, SwitchType, AnchorName, EnumClass, CaseLabels;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(TEXT("add_switch"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("switch_type"), SwitchType) || SwitchType.IsEmpty())
            return JsonError(TEXT("add_switch"), TEXT("missing_field"), TEXT("switch_type"));
        if (!JsonObject->TryGetStringField(TEXT("anchor_name"), AnchorName) || AnchorName.IsEmpty())
            return JsonError(TEXT("add_switch"), TEXT("missing_field"), TEXT("anchor_name"));
        JsonObject->TryGetStringField(TEXT("enum_class"), EnumClass);
        JsonObject->TryGetStringField(TEXT("case_labels"), CaseLabels);

        int32 PosX = 0, PosY = 0, CaseCount = 2;
        JsonObject->TryGetNumberField(TEXT("position_x"), PosX);
        JsonObject->TryGetNumberField(TEXT("position_y"), PosY);
        JsonObject->TryGetNumberField(TEXT("case_count"), CaseCount);
        FString GraphName;
        JsonObject->TryGetStringField(TEXT("graph_name"), GraphName);

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, SwitchType, AnchorName, PosX, PosY, EnumClass, CaseCount, CaseLabels, GraphName]() mutable
            {
                Promise.SetValue(AddSwitchOnGameThread(Blueprint, SwitchType, AnchorName, PosX, PosY, EnumClass, CaseCount, CaseLabels, GraphName));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("add_switch"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- add_sequence (v7.2) ---
    if (Command.Equals(TEXT("add_sequence"), ESearchCase::IgnoreCase))
    {
        FString Blueprint, AnchorName;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(TEXT("add_sequence"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("anchor_name"), AnchorName) || AnchorName.IsEmpty())
            return JsonError(TEXT("add_sequence"), TEXT("missing_field"), TEXT("anchor_name"));

        int32 PosX = 0, PosY = 0, ThenCount = 2;
        JsonObject->TryGetNumberField(TEXT("position_x"), PosX);
        JsonObject->TryGetNumberField(TEXT("position_y"), PosY);
        JsonObject->TryGetNumberField(TEXT("then_count"), ThenCount);
        FString GraphName;
        JsonObject->TryGetStringField(TEXT("graph_name"), GraphName);

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, AnchorName, PosX, PosY, ThenCount, GraphName]() mutable
            {
                Promise.SetValue(AddSequenceOnGameThread(Blueprint, AnchorName, PosX, PosY, ThenCount, GraphName));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("add_sequence"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- add_make_array (v7.2) ---
    if (Command.Equals(TEXT("add_make_array"), ESearchCase::IgnoreCase))
    {
        FString Blueprint, AnchorName;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(TEXT("add_make_array"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("anchor_name"), AnchorName) || AnchorName.IsEmpty())
            return JsonError(TEXT("add_make_array"), TEXT("missing_field"), TEXT("anchor_name"));

        int32 PosX = 0, PosY = 0, NumInputs = 1;
        JsonObject->TryGetNumberField(TEXT("position_x"), PosX);
        JsonObject->TryGetNumberField(TEXT("position_y"), PosY);
        JsonObject->TryGetNumberField(TEXT("num_inputs"), NumInputs);
        FString GraphName;
        JsonObject->TryGetStringField(TEXT("graph_name"), GraphName);

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, AnchorName, PosX, PosY, NumInputs, GraphName]() mutable
            {
                Promise.SetValue(AddMakeArrayOnGameThread(Blueprint, AnchorName, PosX, PosY, NumInputs, GraphName));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("add_make_array"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- add_select (v7.2) ---
    if (Command.Equals(TEXT("add_select"), ESearchCase::IgnoreCase))
    {
        FString Blueprint, AnchorName;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(TEXT("add_select"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("anchor_name"), AnchorName) || AnchorName.IsEmpty())
            return JsonError(TEXT("add_select"), TEXT("missing_field"), TEXT("anchor_name"));

        int32 PosX = 0, PosY = 0, NumOptions = 2;
        JsonObject->TryGetNumberField(TEXT("position_x"), PosX);
        JsonObject->TryGetNumberField(TEXT("position_y"), PosY);
        JsonObject->TryGetNumberField(TEXT("num_options"), NumOptions);
        FString GraphName;
        JsonObject->TryGetStringField(TEXT("graph_name"), GraphName);

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, AnchorName, PosX, PosY, NumOptions, GraphName]() mutable
            {
                Promise.SetValue(AddSelectOnGameThread(Blueprint, AnchorName, PosX, PosY, NumOptions, GraphName));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("add_select"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- add_make_struct (v7.3) ---
    if (Command.Equals(TEXT("add_make_struct"), ESearchCase::IgnoreCase))
    {
        FString Blueprint, StructType, AnchorName;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(TEXT("add_make_struct"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("struct_type"), StructType) || StructType.IsEmpty())
            return JsonError(TEXT("add_make_struct"), TEXT("missing_field"), TEXT("struct_type"));
        if (!JsonObject->TryGetStringField(TEXT("anchor_name"), AnchorName) || AnchorName.IsEmpty())
            return JsonError(TEXT("add_make_struct"), TEXT("missing_field"), TEXT("anchor_name"));

        int32 PosX = 0, PosY = 0;
        JsonObject->TryGetNumberField(TEXT("position_x"), PosX);
        JsonObject->TryGetNumberField(TEXT("position_y"), PosY);

        FString GraphName;
        JsonObject->TryGetStringField(TEXT("graph_name"), GraphName);

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, StructType, AnchorName, PosX, PosY, GraphName]() mutable
            {
                Promise.SetValue(AddMakeStructOnGameThread(Blueprint, StructType, AnchorName, PosX, PosY, GraphName));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("add_make_struct"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- add_break_struct (v7.3) ---
    if (Command.Equals(TEXT("add_break_struct"), ESearchCase::IgnoreCase))
    {
        FString Blueprint, StructType, AnchorName;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(TEXT("add_break_struct"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("struct_type"), StructType) || StructType.IsEmpty())
            return JsonError(TEXT("add_break_struct"), TEXT("missing_field"), TEXT("struct_type"));
        if (!JsonObject->TryGetStringField(TEXT("anchor_name"), AnchorName) || AnchorName.IsEmpty())
            return JsonError(TEXT("add_break_struct"), TEXT("missing_field"), TEXT("anchor_name"));

        int32 PosX = 0, PosY = 0;
        JsonObject->TryGetNumberField(TEXT("position_x"), PosX);
        JsonObject->TryGetNumberField(TEXT("position_y"), PosY);

        FString GraphName;
        JsonObject->TryGetStringField(TEXT("graph_name"), GraphName);

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, StructType, AnchorName, PosX, PosY, GraphName]() mutable
            {
                Promise.SetValue(AddBreakStructOnGameThread(Blueprint, StructType, AnchorName, PosX, PosY, GraphName));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("add_break_struct"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- save_blueprint (v7.8) ---
    if (Command.Equals(TEXT("save_blueprint"), ESearchCase::IgnoreCase))
    {
        FString Blueprint;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(TEXT("save_blueprint"), TEXT("missing_field"), TEXT("blueprint"));

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint]() mutable
            {
                Promise.SetValue(SaveBlueprintOnGameThread(Blueprint));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("save_blueprint"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- add_event_dispatcher (v7.6) ---
    if (Command.Equals(TEXT("add_event_dispatcher"), ESearchCase::IgnoreCase))
    {
        FString Blueprint, DispatcherName;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(TEXT("add_event_dispatcher"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("dispatcher_name"), DispatcherName) || DispatcherName.IsEmpty())
            return JsonError(TEXT("add_event_dispatcher"), TEXT("missing_field"), TEXT("dispatcher_name"));

        TArray<FString> ParamNames, ParamTypes;
        const TArray<TSharedPtr<FJsonValue>>* ParamsArray = nullptr;
        if (JsonObject->TryGetArrayField(TEXT("params"), ParamsArray) && ParamsArray != nullptr)
        {
            for (const TSharedPtr<FJsonValue>& Item : *ParamsArray)
            {
                const TSharedPtr<FJsonObject>* ParamObjPtr = nullptr;
                if (Item.IsValid() && Item->TryGetObject(ParamObjPtr) && ParamObjPtr != nullptr)
                {
                    FString PName, PType;
                    (*ParamObjPtr)->TryGetStringField(TEXT("name"), PName);
                    (*ParamObjPtr)->TryGetStringField(TEXT("type"), PType);
                    if (!PName.IsEmpty() && !PType.IsEmpty())
                    {
                        ParamNames.Add(PName);
                        ParamTypes.Add(PType);
                    }
                }
            }
        }

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, DispatcherName, ParamNames, ParamTypes]() mutable
            {
                Promise.SetValue(AddEventDispatcherOnGameThread(Blueprint, DispatcherName, ParamNames, ParamTypes));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("add_event_dispatcher"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- migrate_dispatchers (v8.0.2 ISSUE-1 + v8.1.0 ghost recreate) ---
    if (Command.Equals(TEXT("migrate_dispatchers"), ESearchCase::IgnoreCase))
    {
        FString Blueprint;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(TEXT("migrate_dispatchers"), TEXT("missing_field"), TEXT("blueprint"));
        bool bRecreateGhosts = false;
        JsonObject->TryGetBoolField(TEXT("recreate_ghosts"), bRecreateGhosts);

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, bRecreateGhosts]() mutable
            {
                Promise.SetValue(MigrateDispatchersOnGameThread(Blueprint, bRecreateGhosts));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("migrate_dispatchers"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- delete_event_dispatcher (v8.0.1 OPEN-1) ---
    if (Command.Equals(TEXT("delete_event_dispatcher"), ESearchCase::IgnoreCase))
    {
        FString Blueprint, DispatcherName;
        if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
            return JsonError(TEXT("delete_event_dispatcher"), TEXT("missing_field"), TEXT("blueprint"));
        if (!JsonObject->TryGetStringField(TEXT("dispatcher_name"), DispatcherName) || DispatcherName.IsEmpty())
            return JsonError(TEXT("delete_event_dispatcher"), TEXT("missing_field"), TEXT("dispatcher_name"));

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Blueprint, DispatcherName]() mutable
            {
                Promise.SetValue(DeleteEventDispatcherOnGameThread(Blueprint, DispatcherName));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("delete_event_dispatcher"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- add_call_dispatcher / add_bind_dispatcher / add_unbind_dispatcher (v7.6) ---
    // Shared shape: same JSON fields, dispatch by command name to the templated helper.
    {
        const bool bCall   = Command.Equals(TEXT("add_call_dispatcher"),   ESearchCase::IgnoreCase);
        const bool bBind   = Command.Equals(TEXT("add_bind_dispatcher"),   ESearchCase::IgnoreCase);
        const bool bUnbind = Command.Equals(TEXT("add_unbind_dispatcher"), ESearchCase::IgnoreCase);
        if (bCall || bBind || bUnbind)
        {
            const TCHAR* CmdName = bCall ? TEXT("add_call_dispatcher")
                                  : bBind ? TEXT("add_bind_dispatcher")
                                          : TEXT("add_unbind_dispatcher");

            FString Blueprint, DispatcherName, AnchorName;
            if (!JsonObject->TryGetStringField(TEXT("blueprint"), Blueprint) || Blueprint.IsEmpty())
                return JsonError(CmdName, TEXT("missing_field"), TEXT("blueprint"));
            if (!JsonObject->TryGetStringField(TEXT("dispatcher_name"), DispatcherName) || DispatcherName.IsEmpty())
                return JsonError(CmdName, TEXT("missing_field"), TEXT("dispatcher_name"));
            if (!JsonObject->TryGetStringField(TEXT("anchor_name"), AnchorName) || AnchorName.IsEmpty())
                return JsonError(CmdName, TEXT("missing_field"), TEXT("anchor_name"));

            int32 PosX = 0, PosY = 0;
            JsonObject->TryGetNumberField(TEXT("position_x"), PosX);
            JsonObject->TryGetNumberField(TEXT("position_y"), PosY);
            FString GraphName;
            JsonObject->TryGetStringField(TEXT("graph_name"), GraphName);   // v7.7.1

            TPromise<FString> Promise;
            TFuture<FString> Future = Promise.GetFuture();
            AsyncTask(ENamedThreads::GameThread,
                [Promise = MoveTemp(Promise), CmdName, bCall, bBind, bUnbind, Blueprint, DispatcherName, AnchorName, PosX, PosY, GraphName]() mutable
                {
                    FString Result;
                    if (bCall)
                        Result = AddDelegateNodeOnGameThread<UK2Node_CallDelegate>(CmdName, TEXT("K2Node_CallDelegate"), Blueprint, DispatcherName, AnchorName, PosX, PosY, GraphName);
                    else if (bBind)
                        Result = AddDelegateNodeOnGameThread<UK2Node_AddDelegate>(CmdName, TEXT("K2Node_AddDelegate"), Blueprint, DispatcherName, AnchorName, PosX, PosY, GraphName);
                    else
                        Result = AddDelegateNodeOnGameThread<UK2Node_RemoveDelegate>(CmdName, TEXT("K2Node_RemoveDelegate"), Blueprint, DispatcherName, AnchorName, PosX, PosY, GraphName);
                    Promise.SetValue(MoveTemp(Result));
                });
            if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
                return JsonError(CmdName, TEXT("game_thread_timeout"));
            return Future.Get();
        }
    }

    // --- v8.1 read_log_capture / clear_log_capture (no game-thread marshaling — thread-safe) ---
    if (Command.Equals(TEXT("read_log_capture"), ESearchCase::IgnoreCase))
    {
        int32 MaxLines = 100;
        JsonObject->TryGetNumberField(TEXT("max_lines"), MaxLines);
        FString CategoryFilter, VerbosityFilter, Substring;
        JsonObject->TryGetStringField(TEXT("category"), CategoryFilter);
        JsonObject->TryGetStringField(TEXT("verbosity"), VerbosityFilter);
        JsonObject->TryGetStringField(TEXT("contains"), Substring);
        return ReadLogCaptureSync(MaxLines, CategoryFilter, VerbosityFilter, Substring);
    }
    if (Command.Equals(TEXT("clear_log_capture"), ESearchCase::IgnoreCase))
    {
        return ClearLogCaptureSync();
    }

    // --- v8.2 PIE control ---
    if (Command.Equals(TEXT("start_pie"), ESearchCase::IgnoreCase)
        || Command.Equals(TEXT("stop_pie"), ESearchCase::IgnoreCase)
        || Command.Equals(TEXT("is_pie_running"), ESearchCase::IgnoreCase))
    {
        const TCHAR* CmdName = Command.Equals(TEXT("start_pie"), ESearchCase::IgnoreCase) ? TEXT("start_pie")
                             : Command.Equals(TEXT("stop_pie"),  ESearchCase::IgnoreCase) ? TEXT("stop_pie")
                                                                                          : TEXT("is_pie_running");
        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        const FString CmdCopy = Command;   // capture as FString (TCHAR* lifetime)
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), CmdCopy]() mutable
            {
                if (CmdCopy.Equals(TEXT("start_pie"), ESearchCase::IgnoreCase))
                    Promise.SetValue(StartPIEOnGameThread());
                else if (CmdCopy.Equals(TEXT("stop_pie"), ESearchCase::IgnoreCase))
                    Promise.SetValue(StopPIEOnGameThread());
                else
                    Promise.SetValue(IsPIERunningOnGameThread());
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(CmdName, TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- v8.3 pie_press_key (v9.9.0 extends with duration_sec) ---
    if (Command.Equals(TEXT("pie_press_key"), ESearchCase::IgnoreCase))
    {
        FString KeyName;
        if (!JsonObject->TryGetStringField(TEXT("key"), KeyName) || KeyName.IsEmpty())
            return JsonError(TEXT("pie_press_key"), TEXT("missing_field"), TEXT("key"));
        int32 PlayerIndex = 0;
        JsonObject->TryGetNumberField(TEXT("player_index"), PlayerIndex);
        double DurationSec = 0.0;
        JsonObject->TryGetNumberField(TEXT("duration_sec"), DurationSec);

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), KeyName, PlayerIndex, DurationSec]() mutable
            {
                Promise.SetValue(PiePressKeyOnGameThread(KeyName, PlayerIndex, static_cast<float>(DurationSec)));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("pie_press_key"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- v9.9.0 pie_set_player_location ---
    if (Command.Equals(TEXT("pie_set_player_location"), ESearchCase::IgnoreCase))
    {
        const TArray<TSharedPtr<FJsonValue>>* LocArr = nullptr;
        if (!JsonObject->TryGetArrayField(TEXT("location"), LocArr) || LocArr->Num() < 3)
            return JsonError(TEXT("pie_set_player_location"), TEXT("missing_field"),
                TEXT("location must be [X,Y,Z]"));
        const double X = (*LocArr)[0]->AsNumber();
        const double Y = (*LocArr)[1]->AsNumber();
        const double Z = (*LocArr)[2]->AsNumber();
        int32 PlayerIndex = 0;
        JsonObject->TryGetNumberField(TEXT("player_index"), PlayerIndex);

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), X, Y, Z, PlayerIndex]() mutable
            {
                Promise.SetValue(PieSetPlayerLocationOnGameThread(
                    static_cast<float>(X), static_cast<float>(Y), static_cast<float>(Z), PlayerIndex));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("pie_set_player_location"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- v9.9.0 pie_move_player (v9.10.0 adds face_movement) ---
    if (Command.Equals(TEXT("pie_move_player"), ESearchCase::IgnoreCase))
    {
        const TArray<TSharedPtr<FJsonValue>>* DirArr = nullptr;
        if (!JsonObject->TryGetArrayField(TEXT("direction"), DirArr) || DirArr->Num() < 3)
            return JsonError(TEXT("pie_move_player"), TEXT("missing_field"),
                TEXT("direction must be [X,Y,Z]"));
        const double DX = (*DirArr)[0]->AsNumber();
        const double DY = (*DirArr)[1]->AsNumber();
        const double DZ = (*DirArr)[2]->AsNumber();
        double DurationSec = 1.0;
        JsonObject->TryGetNumberField(TEXT("duration_sec"), DurationSec);
        double InputScale = 1.0;
        JsonObject->TryGetNumberField(TEXT("scale"), InputScale);
        int32 PlayerIndex = 0;
        JsonObject->TryGetNumberField(TEXT("player_index"), PlayerIndex);
        bool bFaceMovement = false;
        JsonObject->TryGetBoolField(TEXT("face_movement"), bFaceMovement);   // v9.10.0

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), DX, DY, DZ, DurationSec, InputScale, PlayerIndex, bFaceMovement]() mutable
            {
                Promise.SetValue(PieMovePlayerOnGameThread(
                    static_cast<float>(DX), static_cast<float>(DY), static_cast<float>(DZ),
                    static_cast<float>(DurationSec), static_cast<float>(InputScale), PlayerIndex, bFaceMovement));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("pie_move_player"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    // --- v9.10.0 pie_set_player_rotation ---
    if (Command.Equals(TEXT("pie_set_player_rotation"), ESearchCase::IgnoreCase))
    {
        const TArray<TSharedPtr<FJsonValue>>* RotArr = nullptr;
        if (!JsonObject->TryGetArrayField(TEXT("rotation"), RotArr) || RotArr->Num() < 3)
            return JsonError(TEXT("pie_set_player_rotation"), TEXT("missing_field"),
                TEXT("rotation must be [Pitch, Yaw, Roll]"));
        const double Pitch = (*RotArr)[0]->AsNumber();
        const double Yaw   = (*RotArr)[1]->AsNumber();
        const double Roll  = (*RotArr)[2]->AsNumber();
        int32 PlayerIndex = 0;
        JsonObject->TryGetNumberField(TEXT("player_index"), PlayerIndex);

        TPromise<FString> Promise;
        TFuture<FString> Future = Promise.GetFuture();
        AsyncTask(ENamedThreads::GameThread,
            [Promise = MoveTemp(Promise), Pitch, Yaw, Roll, PlayerIndex]() mutable
            {
                Promise.SetValue(PieSetPlayerRotationOnGameThread(
                    static_cast<float>(Pitch), static_cast<float>(Yaw), static_cast<float>(Roll), PlayerIndex));
            });
        if (!Future.WaitFor(FTimespan::FromSeconds(kGameThreadTimeoutSeconds)))
            return JsonError(TEXT("pie_set_player_rotation"), TEXT("game_thread_timeout"));
        return Future.Get();
    }

    return FString::Printf(
        TEXT("{\"ok\":false,\"error\":\"unknown_command\",\"command\":%s}\n"),
        *EscapeJsonString(Command));
}
