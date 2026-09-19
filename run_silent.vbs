Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
WshShell.CurrentDirectory = FSO.GetParentFolderName(WScript.ScriptFullName)
WshShell.Run "python -m tracker.server", 0, False
WScript.Sleep 1500
WshShell.Run "http://127.0.0.1:8778", 1, False
