!include "LogicLib.nsh"

; Check only files in the selected installation, without modifying them or
; terminating unrelated Python/Ollama processes. Silent updates fail closed.
!macro LocalMindRequireWritable RELATIVE LABEL
  Push $0
  Push $1
  ${LABEL}_retry:
  IfFileExists "$INSTDIR\${RELATIVE}" 0 ${LABEL}_done
  System::Call 'kernel32::CreateFileW(w "$INSTDIR\${RELATIVE}", i 0x40000000, i 0, p 0, i 3, i 0, p 0) p.r0'
  ${If} $0 == -1
    System::Call 'kernel32::GetLastError() i.r1'
    DetailPrint "Cannot replace $INSTDIR\${RELATIVE} (Windows error $1)."
    IfSilent ${LABEL}_cancel
    MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "LocalMind cannot replace ${RELATIVE} yet.$\r$\n$\r$\nClose LocalMind, including mini chat, and try again. If a previous version left its background service running, restart Windows before installing.$\r$\n$\r$\nFile: $INSTDIR\${RELATIVE}$\r$\nWindows error: $1" IDRETRY ${LABEL}_retry
    ${LABEL}_cancel:
    Pop $1
    Pop $0
    SetErrorLevel 2
    Abort "LocalMind files are still in use or are not writable. Installation stopped before replacing files."
  ${Else}
    System::Call 'kernel32::CloseHandle(p r0)'
  ${EndIf}
  ${LABEL}_done:
  Pop $1
  Pop $0
!macroend

!macro NSIS_HOOK_PREINSTALL
  !insertmacro LocalMindRequireWritable "localmind-app.exe" lm_app
  !insertmacro LocalMindRequireWritable "localmind-sidecar.exe" lm_sidecar
  !insertmacro LocalMindRequireWritable "ollama-runtime\ollama.exe" lm_ollama
!macroend
