// Copyright (c) 2026 Hal Xu. License: TBD.

#include "BlueprintMCPModule.h"
#include "TCPServer.h"
#include "HAL/RunnableThread.h"
#include "Logging/LogMacros.h"

DEFINE_LOG_CATEGORY_STATIC(LogBlueprintMCP, Log, All);

IMPLEMENT_MODULE(FBlueprintMCPModule, BlueprintMCP);

void FBlueprintMCPModule::StartupModule()
{
    UE_LOG(LogBlueprintMCP, Log, TEXT("BlueprintMCP starting"));
    StartTCPServer();
}

void FBlueprintMCPModule::ShutdownModule()
{
    UE_LOG(LogBlueprintMCP, Log, TEXT("BlueprintMCP stopping"));
    StopTCPServer();
}

void FBlueprintMCPModule::StartTCPServer()
{
    // Port 55558 — deliberately one above chongdashu/unreal-mcp's 55557
    // so the two can coexist if a user has both installed.
    const int32 Port = 55558;
    TCPServer = new FTCPServerRunnable(Port);
    TCPServerThread = FRunnableThread::Create(TCPServer, TEXT("BlueprintMCP_TCPServer"));
    UE_LOG(LogBlueprintMCP, Log, TEXT("BlueprintMCP TCP server thread spawned on port %d"), Port);
}

void FBlueprintMCPModule::StopTCPServer()
{
    if (TCPServer != nullptr)
    {
        TCPServer->Stop();
    }
    if (TCPServerThread != nullptr)
    {
        TCPServerThread->Kill(true);
        delete TCPServerThread;
        TCPServerThread = nullptr;
    }
    if (TCPServer != nullptr)
    {
        delete TCPServer;
        TCPServer = nullptr;
    }
}
