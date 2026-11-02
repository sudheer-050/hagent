Option Explicit
Dim shell, files, root, python, script, result
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
root = files.GetParentFolderName(files.GetParentFolderName(WScript.ScriptFullName))
python = files.BuildPath(root, ".venv\Scripts\python.exe")
script = files.BuildPath(root, "scripts\restart_hagent.py")
shell.CurrentDirectory = root
result = shell.Run("""" & python & """ """ & script & """", 0, True)
If result <> 0 Then
    MsgBox "Hagent could not restart. Finish running tasks and close its terminal sessions, then try again. See output\hagent-restart.log for server errors.", vbExclamation, "Hagent"
Else
    MsgBox "Hagent restarted. Return to Runtimes and click reload to check your connections.", vbInformation, "Hagent"
End If
