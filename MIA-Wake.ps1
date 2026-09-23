param(
  [string]$DashboardUrl = 'https://command.jarvis-reyes.de/',
  [string]$Phrase = 'Hey Mia'
)
$ErrorActionPreference = 'Stop'

function New-Recognizer {
  Add-Type -AssemblyName System.Speech
  $installed = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers()
  $de = $installed | Where-Object { $_.Culture.Name -like 'de-*' } | Select-Object -First 1
  if ($de) { return New-Object System.Speech.Recognition.SpeechRecognitionEngine($de) }
  return New-Object System.Speech.Recognition.SpeechRecognitionEngine
}

$rec = New-Recognizer
$choices = New-Object System.Speech.Recognition.Choices
$choices.Add($Phrase)
$gb = New-Object System.Speech.Recognition.GrammarBuilder($choices)
try { $gb.Culture = $rec.RecognizerInfo.Culture } catch {}
$grammar = New-Object System.Speech.Recognition.Grammar($gb)
$rec.LoadGrammar($grammar)
$rec.SetInputToDefaultAudioDevice()

$cooldownUntil = [DateTime]::MinValue
Register-ObjectEvent -InputObject $rec -EventName SpeechRecognized -SourceIdentifier 'MIA.Wake' -Action {
  $r = $Event.SourceEventArgs.Result
  if ($r -and $r.Confidence -ge 0.55 -and [DateTime]::UtcNow -ge $script:cooldownUntil) {
    $script:cooldownUntil = [DateTime]::UtcNow.AddSeconds(4)
    $url = $using:DashboardUrl
    if ($url.Contains('?')) { $url += '&wake=1' } else { $url += '?wake=1' }
    Start-Process $url
  }
} | Out-Null

Write-Host "MIA Wake lauscht auf '$Phrase'." -ForegroundColor Green
$rec.RecognizeAsync([System.Speech.Recognition.RecognizeMode]::Multiple)
try {
  while ($true) { Wait-Event -Timeout 5 | Out-Null }
} finally {
  try { $rec.RecognizeAsyncStop() } catch {}
  Unregister-Event -SourceIdentifier 'MIA.Wake' -ErrorAction SilentlyContinue
  $rec.Dispose()
}
