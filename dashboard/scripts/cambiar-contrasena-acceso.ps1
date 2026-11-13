<#
.SYNOPSIS
  Cambia la contraseña compartida de acceso sin mostrarla ni dejarla en el historial.

.DESCRIPTION
  Pide la contraseña nueva dos veces, de forma oculta (Read-Host -AsSecureString: no se muestra, no entra en el
  historial de PowerShell ni en una transcripción). Comprueba los requisitos, guarda una copia de la anterior junto
  al archivo y reescribe access-password en su sitio: el frontend lo lee en cada petición y ve el cambio al momento,
  sin recrear el contenedor (también con un montaje de archivo en Linux). Después comprueba que la instalación
  acepta la contraseña nueva.

  Cambiar la contraseña cierra todas las sesiones abiertas: hay que volver a entrar en cada navegador.
  No toca datos deportivos ni lanza actualizaciones.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\dashboard\scripts\cambiar-contrasena-acceso.ps1
#>
param(
  [string]$PasswordFile = '',
  [string]$Url = 'http://127.0.0.1:3000',
  # Solo para pruebas automáticas; en uso normal se piden por teclado.
  [Security.SecureString]$NewPassword,
  [Security.SecureString]$Confirmation
)
$ErrorActionPreference = 'Stop'
if (-not $PasswordFile -and $env:RJ_PROD_SECRET_DIR) {
  $PasswordFile = Join-Path $env:RJ_PROD_SECRET_DIR 'access-password'
}
if (-not $PasswordFile) { throw 'Indica -PasswordFile o configura RJ_PROD_SECRET_DIR con la carpeta externa de secretos.' }
if (-not $PasswordFile -and $env:RJ_PROD_SECRET_DIR) {
  $PasswordFile = Join-Path $env:RJ_PROD_SECRET_DIR 'access-password'
}
if (-not $PasswordFile) { throw 'Indica -PasswordFile o configura RJ_PROD_SECRET_DIR con la carpeta externa de secretos.' }

function ConvertTo-Plain([Security.SecureString]$Secure) {
  $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
  try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
  finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}

$PasswordFile = (Resolve-Path -LiteralPath $PasswordFile).Path
if (-not $NewPassword) { $NewPassword = Read-Host 'Nueva contraseña de acceso' -AsSecureString }
if (-not $Confirmation) { $Confirmation = Read-Host 'Repítela' -AsSecureString }
$new = ConvertTo-Plain $NewPassword
$again = ConvertTo-Plain $Confirmation
try {
  if ($new -cne $again) { throw 'Las dos contraseñas no coinciden. No se ha cambiado nada.' }
  if ($new.Length -lt 12 -or $new.Length -gt 256 -or $new -cnotmatch '^[\x21-\x7e]+$') {
    throw 'Debe tener de 12 a 256 caracteres ASCII imprimibles: sin espacios, eñes ni tildes. No se ha cambiado nada.'
  }
  $previous = [IO.File]::ReadAllBytes($PasswordFile)
  if ([Text.Encoding]::ASCII.GetString($previous).Trim() -ceq $new) { throw 'Es la contraseña actual. No se ha cambiado nada.' }

  $backup = "$PasswordFile.anterior-" + (Get-Date -Format 'yyyyMMdd-HHmmss')
  [IO.File]::WriteAllBytes($backup, $previous)
  # En su sitio (el mismo archivo, sin salto de línea): visible al momento dentro del contenedor.
  [IO.File]::WriteAllBytes($PasswordFile, [Text.Encoding]::ASCII.GetBytes($new))
  Write-Host "Contraseña guardada en $PasswordFile (copia de la anterior: $backup)."

  # Comprobación: abre y descarta una sesión con la contraseña nueva. La contraseña va en el cuerpo, nunca en la URL.
  $body = @{ password = $new } | ConvertTo-Json -Compress
  try {
    $response = Invoke-WebRequest -UseBasicParsing -Method Post -Uri "$Url/api/v1/session" -Body $body `
      -ContentType 'application/json' -Headers @{ Origin = $Url } -TimeoutSec 15
    $status = [int]$response.StatusCode
  } catch {
    $status = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 }
  }
  $body = $null
  switch ($status) {
    204 { Write-Host 'Comprobado: la instalación acepta la contraseña nueva, sin recrear el frontend. Todas las sesiones anteriores se han cerrado.' -ForegroundColor Green }
    401 { Write-Warning ("El frontend todavía no acepta la contraseña nueva. Recréalo desde la raíz del monorepositorio con:`n" +
          "  Set-Location deploy/prod; docker compose --env-file .env -f compose.yaml up -d --no-build --no-deps --force-recreate dashboard`n" +
          "Para volver a la anterior: copia $backup sobre $PasswordFile."); exit 2 }
    429 { Write-Warning 'Demasiados intentos recientes: no se ha podido comprobar. Espera 15 minutos y entra con la contraseña nueva.'; exit 3 }
    0 { Write-Warning "No se ha podido contactar con $Url para comprobarla. Comprueba que la instalación está en marcha."; exit 4 }
    default { Write-Warning "Respuesta inesperada al comprobarla: $status."; exit 5 }
  }
} finally {
  $new = $null; $again = $null
  $NewPassword.Dispose(); $Confirmation.Dispose()
}
