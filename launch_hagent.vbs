Option Explicit

Dim shell, files, repoRoot, powerShell, ensureScript, chrome, command
Dim exitCode, request, ready, attempt

Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
repoRoot = files.GetParentFolderName(WScript.ScriptFullName)
powerShell = shell.ExpandEnvironmentStrings("%SystemRoot%") & "\System32\WindowsPowerShell\v1.0\powershell.exe"
ensureScript = files.BuildPath(repoRoot, "scripts\ensure_running.ps1")
chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"

If Not files.FileExists(chrome) Then
    MsgBox "Chrome was not found at " & chrome, vbCritical, "Hagent"
    WScript.Quit 1
End If

command = """" & powerShell & """ -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File """ & ensureScript & """"
exitCode = shell.Run(command, 0, True)
If exitCode <> 0 Then
    MsgBox "Hagent could not start. Check " & files.BuildPath(repoRoot, "hagent-watchdog-error.log"), vbCritical, "Hagent"
    WScript.Quit exitCode
End If

ready = False
For attempt = 1 To 30
    On Error Resume Next
    Set request = CreateObject("MSXML2.XMLHTTP.6.0")
    request.Open "GET", "http://127.0.0.1:8000/", False
    request.Send
    ready = (Err.Number = 0)
    If ready Then ready = (request.Status = 200 Or request.Status = 302 Or request.Status = 401)
    Err.Clear
    On Error GoTo 0
    If ready Then Exit For
    WScript.Sleep 1000
Next

If Not ready Then
    MsgBox "Hagent did not become ready within 30 seconds. Check " & files.BuildPath(repoRoot, "hagent-server-error.log"), vbCritical, "Hagent"
    WScript.Quit 1
End If

shell.Run """" & chrome & """ --app=http://127.0.0.1:8000/", 1, False
