Option Explicit

Dim shell, fso, appDir, appFile, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

appDir = fso.GetParentFolderName(WScript.ScriptFullName)
appFile = appDir & "\app.py"

If Not fso.FileExists(appFile) Then
    MsgBox "未找到程序文件：" & appFile, vbCritical, "轻舟下载器"
    WScript.Quit 1
End If

command = "pythonw " & Chr(34) & appFile & Chr(34)
shell.CurrentDirectory = appDir
shell.Run command, 0, False
