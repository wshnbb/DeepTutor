; Runtime imports create bytecode that is not in NSIS's packaged file list.
; The embedded interpreter is application code; user data lives outside it.
!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $INSTDIR != ""
    RMDir /r "$INSTDIR\runtime\python"
    RMDir "$INSTDIR\runtime"
    RMDir "$INSTDIR"
  ${EndIf}
!macroend
