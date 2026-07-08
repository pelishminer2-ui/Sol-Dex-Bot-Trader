' run_hidden.vbs - Advanced: start bot with no console (server stays running; does not stop on browser close)
Option Explicit

Dim fso, shell, root, ps1
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
ps1 = root & "\launch.ps1"

shell.CurrentDirectory = root
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & ps1 & """ -Detach -NoPause", 0, False
