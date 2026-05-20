// Copyright (c) 2026 Hal Xu. License: TBD.

#pragma once

#include "CoreMinimal.h"
#include "Modules/ModuleManager.h"

/**
 * BlueprintMCP module entry point.
 *
 * On editor startup: opens a TCP listener on TCP_PORT (see TCPServer.cpp).
 * Receives JSON commands, dispatches them to handlers, returns JSON responses.
 *
 * v0 spike: only `ping` command supported. More commands wired in as we go.
 */
class FBlueprintMCPModule : public IModuleInterface
{
public:
    // IModuleInterface
    virtual void StartupModule() override;
    virtual void ShutdownModule() override;

private:
    void StartTCPServer();
    void StopTCPServer();

    class FTCPServerRunnable* TCPServer = nullptr;
    class FRunnableThread*    TCPServerThread = nullptr;
};
