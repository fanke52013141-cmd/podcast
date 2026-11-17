Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
Write-Output "== installed voices =="
$voices = $synth.GetInstalledVoices()
foreach ($v in $voices) {
    Write-Output ("{0} | {1}" -f $v.VoiceInfo.Name, $v.VoiceInfo.Culture)
}
$zh = $null
foreach ($v in $voices) {
    $c = [string]$v.VoiceInfo.Culture
    if ($c -like "zh*") { $zh = $v.VoiceInfo.Name; break }
}
if ($zh) {
    $synth.SelectVoice($zh)
    Write-Output ("== selected: {0} ==" -f $zh)
} else {
    Write-Output "== no Chinese voice, using default =="
}
$synth.Rate = 0
$b64 = "5aSn5a625aW977yM5qyi6L+O5pS25ZCs5pys5pyf6IqC55uu44CC5LuK5aSp5ZKx5Lus6IGK5LiA5Liq5oy65pyJ5oSP5oCd55qE6K+d6aKY77yaQUkg5Lya5LiN5Lya5Y+W5Luj5pKt5a6i5Li75oyB5Lq644CC5oiR6KeJ5b6X5ZCn77yM55+t5pyf5YaF5LiN5Lya77yM5L2G5a6D5Lya5pS55Y+Y5YGa5pKt5a6i55qE5pa55byP44CC"
$text = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($b64))
$out = Join-Path (Split-Path $PSScriptRoot -Parent) "tests\fixtures\sapi_zh_sample.wav"
New-Item -ItemType Directory -Force -Path (Split-Path $out) | Out-Null
$synth.SetOutputToWaveFile($out)
$synth.Speak($text)
$synth.Dispose()
Write-Output ("== saved: {0} ==" -f (Resolve-Path $out))
