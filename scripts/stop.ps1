param([switch]$Check, [switch]$Force)
$ErrorActionPreference = 'Stop'
$workspace = (Resolve-Path -LiteralPath (Split-Path $PSScriptRoot -Parent)).Path
Set-Location -LiteralPath $workspace
$python = Join-Path $workspace '.venv\Scripts\python.exe'
$configPath = Join-Path $workspace 'data\launcher.json'
if (-not (Test-Path -LiteralPath $configPath)) { throw 'Missing data/launcher.json. Configure the local launcher before stopping services.' }
$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$pgAdminExe = if ($config.pgadmin_exe) { [IO.Path]::GetFullPath($config.pgadmin_exe) } else { '' }
$launcherPath = Join-Path $workspace 'scripts\launch.py'
$processes = @(Get-CimInstance Win32_Process)
$listener = @(Get-NetTCPConnection -State Listen -LocalPort 8765 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
$servers = @($processes | Where-Object {
    $_.ProcessId -in $listener -and $_.Name -eq 'python.exe' -and
    $_.CommandLine -like '*-m uvicorn app.main:app*' -and
    ($_.CommandLine -like "*$workspace*" -or $_.ExecutablePath -eq $python)
})
# Venv Python may delegate to its base interpreter. Verify a workspace launcher ancestor.
foreach ($candidate in @($processes | Where-Object { $_.ProcessId -in $listener -and $_.Name -eq 'python.exe' -and $_.CommandLine -like '*-m uvicorn app.main:app*' })) {
    if ($candidate.ProcessId -in @($servers.ProcessId)) { continue }
    $ancestor = $candidate
    for ($depth = 0; $depth -lt 5; $depth++) {
        $parentId = $ancestor.ParentProcessId
        $ancestor = $processes | Where-Object { $_.ProcessId -eq $parentId } | Select-Object -First 1
        if (-not $ancestor) { break }
        if ($ancestor.CommandLine -like "*$launcherPath*" -or $ancestor.ExecutablePath -eq $python) { $servers += $candidate; break }
    }
}
if ($listener.Count -and -not $servers.Count) { throw 'Port 8765 belongs to an unverified process. Nothing was stopped.' }
$pgAdmin = @($processes | Where-Object { $_.ExecutablePath -and $_.ExecutablePath -eq $pgAdminExe })
$ollamaCommand = Get-Command ollama.exe -ErrorAction SilentlyContinue
$ollamaRoot = if ($ollamaCommand) { (Split-Path $ollamaCommand.Source -Parent).TrimEnd('\') + '\' } else { $null }
$ollama = @($processes | Where-Object { $ollamaRoot -and $_.ExecutablePath -and $_.ExecutablePath.StartsWith($ollamaRoot, [StringComparison]::OrdinalIgnoreCase) -and $_.Name -like '*ollama*' })
$consoles = @($processes | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like "*$launcherPath*" })
$launchWindows = @($processes | Where-Object { $_.Name -eq 'cmd.exe' -and $_.CommandLine -like "*$workspace*Start-WitnessAI.bat*" })
$cluster = if ($config.postgres_data) { if ([IO.Path]::IsPathRooted($config.postgres_data)) { [IO.Path]::GetFullPath($config.postgres_data) } else { [IO.Path]::GetFullPath((Join-Path $workspace $config.postgres_data)) } } else { $null }
$pgCtl = if ($config.postgres_bin) { Join-Path $config.postgres_bin 'pg_ctl.exe' } else { $null }
if ($cluster -and -not (Test-Path -LiteralPath (Join-Path $cluster 'PG_VERSION'))) { throw 'Configured PostgreSQL cluster was not found. Nothing was stopped.' }
Write-Host "Workbench servers: $($servers.Count); launcher consoles: $($consoles.Count); pgAdmin processes: $($pgAdmin.Count); local Ollama processes: $($ollama.Count)"
Write-Host "PostgreSQL target: $cluster"
if ($Check) { Write-Host 'Check only. No processes were stopped.'; exit 0 }
Write-Host 'Stopping the application first. In-flight work may be interrupted; saved database records and documents are retained.'
foreach ($server in $servers) {
    & $python (Join-Path $PSScriptRoot 'console_signal.py') $server.ProcessId
    if ($LASTEXITCODE -ne 0) { Write-Warning 'Graceful console signal was unavailable for this server.' }
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Process -Id $server.ProcessId -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 1 }
    if (Get-Process -Id $server.ProcessId -ErrorAction SilentlyContinue) {
        if (-not $Force) { throw 'Application is still finishing work. Database was left running. Retry later, or use Stop-WitnessAI.bat -Force to interrupt it.' }
        # Recheck identity immediately before forced termination.
        $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($server.ProcessId)"
        if ($current.CreationDate -eq $server.CreationDate) { Stop-Process -Id $server.ProcessId -Force }
    }
}
if (Get-NetTCPConnection -State Listen -LocalPort 8765 -ErrorAction SilentlyContinue) { throw 'Application port is still active. Database was left running.' }
if ($cluster -and (Test-Path -LiteralPath (Join-Path $cluster 'postmaster.pid'))) {
    & $python (Join-Path $PSScriptRoot 'reconcile_shutdown.py')
    if ($LASTEXITCODE -ne 0) { throw 'Could not record interrupted workflows. Database was left running for inspection.' }
}
# Close pgAdmin before PostgreSQL so its sessions are released. Only the configured executable is matched.
foreach ($item in $pgAdmin) {
    $process = Get-Process -Id $item.ProcessId -ErrorAction SilentlyContinue
    if ($process) { [void]$process.CloseMainWindow() }
}
Start-Sleep -Seconds 2
foreach ($item in $pgAdmin) {
    $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($item.ProcessId)" -ErrorAction SilentlyContinue
    if ($current -and $current.CreationDate -eq $item.CreationDate -and $current.ExecutablePath -eq $pgAdminExe) { Stop-Process -Id $item.ProcessId -Force -ErrorAction SilentlyContinue }
}
if ($cluster -and $pgCtl -and (Test-Path -LiteralPath $pgCtl)) {
    & $pgCtl -D $cluster status *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Host 'Stopping PostgreSQL cleanly (fast shutdown, not immediate shutdown)...'
        & $pgCtl -D $cluster -m fast -w -t 60 stop
        if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL shutdown did not complete. Inspect its console; no forced database termination was attempted.' }
    } else { Write-Host 'Configured PostgreSQL cluster is already stopped.' }
} else { Write-Warning 'No managed PostgreSQL cluster configured; external PostgreSQL services were left alone.' }
foreach ($item in $ollama) {
    $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($item.ProcessId)" -ErrorAction SilentlyContinue
    if ($current -and $current.CreationDate -eq $item.CreationDate -and $current.ExecutablePath.StartsWith($ollamaRoot, [StringComparison]::OrdinalIgnoreCase)) { Stop-Process -Id $item.ProcessId -Force -ErrorAction SilentlyContinue }
}
foreach ($item in @($consoles) + @($launchWindows)) {
    $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($item.ProcessId)" -ErrorAction SilentlyContinue
    if ($current -and $current.CreationDate -eq $item.CreationDate) { Stop-Process -Id $item.ProcessId -Force -ErrorAction SilentlyContinue }
}
Write-Host 'Workbench services and launcher consoles closed. Browser windows were left open.'
exit 0
