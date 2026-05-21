// Copyright (c) 2026 Hal Xu. License: TBD.

#include "BlueprintMCPRunCommandlet.h"
#include "HAL/PlatformProcess.h"
#include "Async/TaskGraphInterfaces.h"
#include "Misc/CoreDelegates.h"
#include "Containers/Ticker.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"

DEFINE_LOG_CATEGORY_STATIC(LogBlueprintMCPRun, Log, All);

FThreadSafeBool UBlueprintMCPRunCommandlet::bShouldExit(false);

UBlueprintMCPRunCommandlet::UBlueprintMCPRunCommandlet()
{
    IsClient = false;
    IsEditor = true;     // we need the editor environment so UnrealEd / IAssetTools / etc. work
    IsServer = false;
    LogToConsole = true;
    ShowErrorCount = true;
}

int32 UBlueprintMCPRunCommandlet::Main(const FString& Params)
{
    UE_LOG(LogBlueprintMCPRun, Log,
        TEXT("BlueprintMCPRunCommandlet starting. TCP server is listening on 55558; "
             "send {\"command\":\"shutdown_editor\"} to exit cleanly."));

    // Force a full asset-registry scan on /Game so list_* and skeleton/sequence
    // lookups don't return empty in headless mode (where Editor's normal
    // background scan may not have completed).
    {
        FAssetRegistryModule& ARMod = FModuleManager::LoadModuleChecked<FAssetRegistryModule>("AssetRegistry");
        IAssetRegistry& AR = ARMod.Get();
        TArray<FString> Paths;
        Paths.Add(TEXT("/Game"));
        UE_LOG(LogBlueprintMCPRun, Log, TEXT("Priming asset registry scan on /Game..."));
        AR.ScanPathsSynchronous(Paths, /*bForceRescan*/ false);
        AR.SearchAllAssets(/*bSynchronousSearch*/ true);
        UE_LOG(LogBlueprintMCPRun, Log, TEXT("Asset registry primed."));
    }

    const double TickHz = 60.0;
    const double TickIntervalSec = 1.0 / TickHz;
    double LastTickTime = FPlatformTime::Seconds();

    // Sleep + pump loop. AsyncTask(ENamedThreads::GameThread, ...) submissions
    // from the TCP thread land in the game thread's task queue — we must
    // drain it each tick or the TCP handler will time out at 10s.
    while (!bShouldExit && !IsEngineExitRequested())
    {
        FPlatformProcess::Sleep(static_cast<float>(TickIntervalSec));

        const double Now = FPlatformTime::Seconds();
        const float DeltaTime = static_cast<float>(Now - LastTickTime);
        LastTickTime = Now;

        // 1. Drain GameThread task queue (this is where AsyncTask payloads run).
        if (FTaskGraphInterface::IsRunning())
        {
            FTaskGraphInterface::Get().ProcessThreadUntilIdle(ENamedThreads::GameThread);
        }

        // 2. Tick FTSTicker so timers + FAssetRegistry deferred work runs.
        FTSTicker::GetCoreTicker().Tick(DeltaTime);
    }

    UE_LOG(LogBlueprintMCPRun, Log, TEXT("BlueprintMCPRunCommandlet exiting (shouldExit=%s)"),
        bShouldExit ? TEXT("true") : TEXT("false"));
    return 0;
}
