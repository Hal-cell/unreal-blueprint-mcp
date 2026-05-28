// Copyright (c) 2026 Hal Xu. License: TBD.

#pragma once

#include "CoreMinimal.h"
#include "HAL/Runnable.h"
#include "HAL/ThreadSafeBool.h"
#include "Misc/OutputDevice.h"

class FSocket;

/**
 * v8.1: FOutputDevice that captures the UE log stream into a thread-safe circular
 * buffer. Installed globally during module startup so `read_log_capture` can
 * return what `PrintString` produced during PIE without any per-call setup.
 *
 * Thread-safety: Serialize() may fire from any thread (game thread, worker
 * threads, render thread). All access guarded by FCriticalSection. Snapshot()
 * returns a copy so the caller doesn't hold the lock during JSON serialization.
 */
class FBlueprintMCPLogCapture : public FOutputDevice
{
public:
    FBlueprintMCPLogCapture() = default;
    virtual ~FBlueprintMCPLogCapture() = default;

    // FOutputDevice — invoked by GLog for every log line
    virtual void Serialize(const TCHAR* V, ELogVerbosity::Type Verbosity, const FName& Category) override;

    /** Copy the last `MaxLines` captured lines (or all if MaxLines <= 0 or >= count). */
    TArray<FString> Snapshot(int32 MaxLines = 1000) const;

    /**
     * v9.25.0 — return (sequence, line) pairs so callers can checkpoint via
     * since_seq. Sequences are monotonic across the entire process lifetime
     * (never reset on Clear, so callers' checkpoints stay valid even if the
     * buffer was wiped in between). MinSeq filters to entries with seq > MinSeq
     * (use 0 for no filter). MaxLines tail-trims AFTER the seq filter.
     */
    TArray<TPair<int64, FString>> SnapshotWithSeq(int64 MinSeq, int32 MaxLines) const;

    /** v9.25.0 — read the next sequence number that will be assigned. Useful
     *  as a baseline checkpoint: read latest_seq, do operation, read again
     *  with since_seq=baseline to get only the new entries. */
    int64 GetLatestSeq() const;

    /** Drop all captured lines. */
    void Clear();

private:
    mutable FCriticalSection Mutex;
    // v9.25.0: each entry is (monotonic seq, formatted line).
    TArray<TPair<int64, FString>> Entries;
    int64 NextSeq = 1;                                  // 1-based; 0 = "no entries seen"
    static constexpr int32 kMaxBufferedLines = 1000;   // circular buffer cap
};

/** Global instance, installed/uninstalled by FBlueprintMCPModule. */
extern FBlueprintMCPLogCapture* GBlueprintMCPLogCapture;

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
