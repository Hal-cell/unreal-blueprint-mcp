// Copyright (c) 2026 Hal Xu. License: TBD.
//
// v9.6.0 — Headless CI test harness.
//
// Run with:  UnrealEditor-Cmd <project>.uproject -run=BlueprintMCPRun -nullrhi -unattended
//
// On entry, the BlueprintMCP module has already loaded and started the TCP
// server on port 55558. This commandlet does nothing except keep the process
// alive in a sleep loop until `shutdown_editor` is sent over TCP — at which
// point bShouldExit flips and Main returns 0.

#pragma once

#include "CoreMinimal.h"
#include "Commandlets/Commandlet.h"
#include "HAL/ThreadSafeBool.h"
#include "BlueprintMCPRunCommandlet.generated.h"

UCLASS()
class UBlueprintMCPRunCommandlet : public UCommandlet
{
    GENERATED_BODY()

public:
    UBlueprintMCPRunCommandlet();

    //~ Begin UCommandlet Interface
    virtual int32 Main(const FString& Params) override;
    //~ End UCommandlet Interface

    /** Set by the `shutdown_editor` TCP command to break out of the sleep loop. */
    static FThreadSafeBool bShouldExit;
};
