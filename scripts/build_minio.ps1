$ErrorActionPreference = 'Stop'
$TaskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $TaskRoot
$Git = (Get-Command git -ErrorAction Stop).Source
$Docker = (Get-Command docker -ErrorAction Stop).Source
$Source = Join-Path $TaskRoot 'vendor/minio'
$Commit = '7a311a3b666a0e5476fbde24d4413220876d9df2'
$Tag = 'RELEASE.2023-12-20T01-00-02Z'
$Image = "ict-minio:$Tag-source"
if (-not (Test-Path -LiteralPath $Source)) {
    & $Git clone --depth 1 --branch $Tag https://github.com/minio/minio.git $Source
    if ($LASTEXITCODE -ne 0) { throw 'Pinned MinIO source download failed.' }
}
$ActualCommit = & $Git -C $Source rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $ActualCommit -ne $Commit) { throw 'MinIO source does not match the pinned release commit.' }
$Changes = & $Git -C $Source status --porcelain
if ($LASTEXITCODE -ne 0 -or $Changes) { throw 'MinIO source has local changes; cannot record it as an unmodified source build.' }
& $Docker build --progress plain -f deploy/minio-source.Dockerfile -t $Image $Source
if ($LASTEXITCODE -ne 0) { throw 'MinIO source image build failed; deployment was not started.' }
$ServerVersion = & $Docker run --rm $Image minio --version
if ($LASTEXITCODE -ne 0 -or ($ServerVersion -join "`n") -notmatch 'RELEASE\.2023-12-20T01-00-02Z') { throw 'MinIO executable verification failed.' }
Write-Output $ServerVersion
# The official entrypoint prepends minio, so select mc explicitly here.
$ClientVersion = & $Docker run --rm --entrypoint mc $Image --version
if ($LASTEXITCODE -ne 0 -or ($ClientVersion -join "`n") -notmatch 'RELEASE\.2023-12-14T00-37-41Z') { throw 'MinIO Client executable verification failed.' }
Write-Output $ClientVersion
& $Docker image inspect $Image | & '.\.venv\Scripts\python.exe' scripts/record_minio_build.py
if ($LASTEXITCODE -ne 0) { throw 'MinIO provenance recording failed.' }
