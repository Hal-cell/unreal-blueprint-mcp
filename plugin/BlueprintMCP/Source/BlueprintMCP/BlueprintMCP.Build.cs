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
        });
    }
}
