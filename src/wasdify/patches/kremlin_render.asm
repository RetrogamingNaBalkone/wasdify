; WASDify original replacement code. NASM -f bin -DSECTION=1..3.
; 16-bit protected-mode code, 386 instructions already required by the game.
; No relocations. BX only: preserve its upper half. Arithmetic below the load
; overwrites flags before they are consumed. Three NOPs are unreachable.
BITS 16
%if SECTION = 1
    times 3 nop
    movzx bx, byte [gs:si]
%elif SECTION = 2
    ; Displacement only for JNE at +AF: +93 - +B1.
    db 0x93 - 0xb1
%elif SECTION = 3
    ; Displacement only for JNE at +C7: +93 - +C9.
    db 0x93 - 0xc9
%else
    %error "SECTION must be 1, 2 or 3"
%endif
