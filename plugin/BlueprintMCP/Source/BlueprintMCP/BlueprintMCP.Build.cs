// Copyright (c) 2026 Hal Xu. License: TBD.

using UnrealBuildTool;

public class BlueprintMCP : ModuleRules
{
    public BlueprintMCP(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = ModuleRules.PCHUsageMode.UseExplicitOrSharedPCHs;

        PublicDependencyModuleNames.AddRange(new string[]
        {
            "Core",
            "CoreUObject",
            "Engine",
            "Sockets",
            "Networking",
        });

        PrivateDependencyModuleNames.AddRange(new string[]
        {
            "Slate",
            "SlateCore",
            "UnrealEd",
            "Json",
            "JsonUtilities",
            // Blueprint / Kismet — needed once we start manipulating Blueprints
            "BlueprintGraph",
            "KismetCompiler",
            "AssetTools",
            // Spike B1+: editor-scripting helpers (UEditorAssetLibrary, etc.)
            "EditorScriptingUtilities",
            // v4: FKey for K2Node_InputKey
            "InputCore",
            // v5: Enhanced Input (UInputAction, UInputMappingContext)
            "EnhancedInput",
            // v5: K2Node_EnhancedInputAction lives in this module
            "InputBlueprintNodes",
            // v9.1.0: asset discovery (IAssetRegistry, FAssetData)
            "AssetRegistry",
            // v9.2.0: AnimGraph node types (StateMachine, State, Transition, SequencePlayer)
            "AnimGraph",
            // v9.3.0: Niagara — UNiagaraSystem (factory class found via FindObject
            // because UNiagaraSystemFactoryNew isn't NIAGARAEDITOR_API-exported)
            "Niagara",
            // v9.4.0: UMG — UWidgetBlueprint + UWidgetBlueprintFactory + UUserWidget
            "UMG",
            "UMGEditor",
            // v9.15.0: Material subsystem — MaterialEditingLibrary, expression
            // helpers, UMaterialFactoryNew (UMaterial itself is Engine-core).
            "MaterialEditor",
        });
    }
}
