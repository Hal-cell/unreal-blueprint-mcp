// Copyright (c) 2026 Hal Xu. License: TBD.

#pragma once

#include "CoreMinimal.h"
#include "HAL/Runnable.h"
#include "HAL/ThreadSafeBool.h"

class FSocket;

/**
 * Minimal blocking TCP server for the BlueprintMCP plugin.
 *
 * Listens on a port, accepts one client at a time, reads newline-terminated
 * JSON commands, dispatches them to handlers, writes JSON responses back.
 *
 * v0 spike scope:
 *   - Only `{"command":"ping"}` is recognized; replies `{"ok":true,"version":"0.0.1"}`
 *
 * Threading: the FRunnable runs on its own thread. Handlers that need to touch
 * UObject state MUST marshal back to the game thread via AsyncTask(
 *   ENamedThreads::GameThread, ...). v0 ping doesn't, so it's safe.
 */
class FTCPServerRunnable : public FRunnable
{
public:
    explicit FTCPServerRunnable(int32 InPort);
    virtual ~FTCPServerRunnable();

    // FRunnable
    virtual bool Init() override;
    virtual uint32 Run() override;
    virtual void Stop() override;

private:
    /** Handle one client: read until newline, dispatch, write response, close. */
    void HandleClient(FSocket* ClientSocket);

    /** Dispatch a single JSON command line, return JSON response string. */
    FString DispatchCommand(const FString& JsonCommandLine);

    int32 Port = 0;
    FSocket* ListenSocket = nullptr;
    FThreadSafeBool bShouldStop;
};
