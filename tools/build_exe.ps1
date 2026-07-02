param(
    [string]$Python = "python",
    [string]$EntryPoint = "..\src\appdata_explorer.py"
)

$ErrorActionPreference = "Stop"

Push-Location $PSScriptRoot
try {
    & $Python -m PyInstaller --onefile --windowed --name ADataExplorer `
        --distpath "..\dist" `
        --workpath "..\build" `
        --specpath ".." `
        $EntryPoint
    Write-Host "Executavel gerado em: $((Resolve-Path '..\dist\ADataExplorer.exe').Path)"
}
finally {
    Pop-Location
}
