param([Parameter(Mandatory=$true)][string]$InputPath, [string]$Language = 'zh-Hans-CN')
$ErrorActionPreference = 'Stop'
# A Python child can inherit PowerShell 7's module search path. Load the
# Windows PowerShell utility module by its trusted installation path.
Import-Module "$PSHOME\Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1"
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$fileType = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$pdfType = [Windows.Data.Pdf.PdfDocument, Windows.Data.Pdf, ContentType=WindowsRuntime]
$streamType = [Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
$decoderType = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$bitmapType = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$resultType = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType=WindowsRuntime]
$engineType = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
$languageType = [Windows.Globalization.Language, Windows.Globalization, ContentType=WindowsRuntime]
$renderType = [Windows.Data.Pdf.PdfPageRenderOptions, Windows.Data.Pdf, ContentType=WindowsRuntime]
$transformType = [Windows.Graphics.Imaging.BitmapTransform, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$pixelFormat = [Windows.Graphics.Imaging.BitmapPixelFormat, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$alphaMode = [Windows.Graphics.Imaging.BitmapAlphaMode, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$orientationMode = [Windows.Graphics.Imaging.ExifOrientationMode, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$colorMode = [Windows.Graphics.Imaging.ColorManagementMode, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$genericTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetGenericArguments().Count -eq 1 -and $_.GetParameters().Count -eq 1
} | Select-Object -First 1
$actionTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and -not $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.FullName -eq 'Windows.Foundation.IAsyncAction'
} | Select-Object -First 1
function Wait-WinRT($Operation, [Type]$ResultType) {
    if ($ResultType) { $task = $genericTask.MakeGenericMethod($ResultType).Invoke($null, @($Operation)) }
    else { $task = $actionTask.Invoke($null, @($Operation)) }
    if (-not $task.Wait(15000)) { throw 'Native OCR operation timed out' }
    if ($ResultType) { return $task.GetAwaiter().GetResult() }
}
$resolvedInput = [IO.Path]::GetFullPath($InputPath)
if (-not [IO.File]::Exists($resolvedInput) -or ([IO.FileInfo]$resolvedInput).Length -gt 5242880) { throw 'Bounded local PDF required' }
$inputStream = [IO.File]::OpenRead($resolvedInput)
$hasher = [Security.Cryptography.SHA256]::Create()
try { $inputHash = [BitConverter]::ToString($hasher.ComputeHash($inputStream)).Replace('-', '').ToLowerInvariant() }
finally { $hasher.Dispose(); $inputStream.Dispose() }
$engine = $engineType::TryCreateFromLanguage([Activator]::CreateInstance($languageType, @($Language)))
if (-not $engine -or $engine.RecognizerLanguage.LanguageTag -ne $Language) { throw 'Requested native OCR language unavailable' }
$file = Wait-WinRT ($fileType::GetFileFromPathAsync($resolvedInput)) $fileType
$pdf = Wait-WinRT ($pdfType::LoadFromFileAsync($file)) $pdfType
if ($pdf.PageCount -lt 1 -or $pdf.PageCount -gt 20 -or $pdf.IsPasswordProtected) { throw 'Unencrypted PDF of at most 20 pages required for OCR' }
$pages = @()
for ($index = 0; $index -lt $pdf.PageCount; $index++) {
    $page = $pdf.GetPage($index)
    $stream = [Activator]::CreateInstance($streamType)
    $bitmap = $null
    try {
        $width, $height = [double]$page.Size.Width, [double]$page.Size.Height
        if ($width -le 0 -or $height -le 0 -or $width -gt 3000 -or $height -gt 3000) { throw 'PDF page geometry out of bounds' }
        $limit = [Math]::Min(2400, $engineType::MaxImageDimension)
        $scale = $limit / [Math]::Max($width, $height)
        $options = [Activator]::CreateInstance($renderType)
        $options.DestinationWidth = [uint32][Math]::Max(1, [Math]::Round($width * $scale))
        $options.DestinationHeight = [uint32][Math]::Max(1, [Math]::Round($height * $scale))
        Wait-WinRT ($page.RenderToStreamAsync($stream, $options)) $null
        $stream.Seek(0)
        $decoder = Wait-WinRT ($decoderType::CreateAsync($stream)) $decoderType
        if ([double]$decoder.PixelWidth * $decoder.PixelHeight -gt 40000000) { throw 'Rendered PDF image too large' }
        # PDF rendering can be affected by desktop DPI. Enforce pixel bounds at
        # the bitmap decoder too, rather than trusting render-option dimensions.
        $transform = [Activator]::CreateInstance($transformType)
        $ratio = $limit / [Math]::Max($decoder.PixelWidth, $decoder.PixelHeight)
        $transform.ScaledWidth = [uint32][Math]::Max(1, [Math]::Round($decoder.PixelWidth * $ratio))
        $transform.ScaledHeight = [uint32][Math]::Max(1, [Math]::Round($decoder.PixelHeight * $ratio))
        $bitmap = Wait-WinRT ($decoder.GetSoftwareBitmapAsync($pixelFormat::Bgra8, $alphaMode::Ignore,
            $transform, $orientationMode::IgnoreExifOrientation, $colorMode::DoNotColorManage)) $bitmapType
        $result = Wait-WinRT ($engine.RecognizeAsync($bitmap)) $resultType
        $lines = @($result.Lines)
        $text = ($lines | ForEach-Object { $_.Text }) -join "`n"
        if ($text.Length -lt 8 -or $text.Length -gt 16000 -or $lines.Count -gt 800) { throw 'Empty or oversized OCR page' }
        $pages += @{ page=$index+1; text=$text; line_count=$lines.Count; width=$bitmap.PixelWidth; height=$bitmap.PixelHeight }
    } finally {
        if ($bitmap) { $bitmap.Dispose() }
        $stream.Dispose()
        $page.Dispose()
    }
}
@{ schema=1; engine='windows.media.ocr'; language=$engine.RecognizerLanguage.LanguageTag; input_sha256=$inputHash;
   confidence_available=$false; requires_visual_review=$true; pages=$pages } | ConvertTo-Json -Depth 5 -Compress
