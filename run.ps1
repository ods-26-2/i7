# Equivalente do run.sh para Windows PowerShell.
#   .\run.ps1 build                                  -> reconstrói a imagem
#   .\run.ps1 scripts/publicador_eventos.py --exemplo -> roda um script dentro do container
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$Imagem = "parkflow-i7"

if ($args.Count -gt 0 -and $args[0] -eq "build") {
    docker build -t $Imagem .
    exit $LASTEXITCODE
}

if (-not (Test-Path .env)) {
    Write-Host "Falta o arquivo .env (copie o .env.example para .env)"
    exit 1
}

New-Item -ItemType Directory -Force saidas, datasetConvertidoYOLO | Out-Null

docker run --rm --env-file .env `
    -v "${PWD}/datasetConvertidoYOLO:/app/datasetConvertidoYOLO" `
    -v "${PWD}/saidas:/app/saidas" `
    -v i7-ocr-cache:/root/.cache/fast-plate-ocr `
    $Imagem @args
exit $LASTEXITCODE