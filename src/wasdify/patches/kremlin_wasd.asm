; Original WASDify code, MIT license. Flat 16-bit NE in-place replacements.
; nasm -f bin -DSECTION=1 -DENG=0 kremlin_wasd.asm -o keyboard-ru.bin
; SECTION: 1 keyboard (195), 2 startup (89), 3 cancel (75), 4 mouse (142),
; 5 angle helper (45), 6 angle hook (3). No runtime assembler required.
; ENG: 0 PKR.EXE, 1 PKE.EXE. No new selector relocations.
BITS 16
%ifndef ENG
%define ENG 0
%endif
%if ENG
%define INPUT 0x199c
%define OBJECT 0x228a
%define KEYS 0x2205
%define MOUSE_X 0x09b2
%define MOUSE_FRACTION 0x09b4
%define PLAYER_DELTA 0x50a
%define HERO_RETURN 0x285a
%else
%define INPUT 0x1639
%define OBJECT 0x21da
%define KEYS 0x2155
%define MOUSE_X 0x08a2
%define MOUSE_FRACTION 0x08a4
%define PLAYER_DELTA 0
%define HERO_RETURN 0x22aa
%endif

%if SECTION = 1
ORG INPUT + 0x18
    jmp short poll_start
    times 8-($-$$) db 0x90
reader: dw 0xffff, 0 ; original NE far-pointer fixup stays at INPUT+0x20
poll_start:
    mov si, bindings
.poll:
    movzx ax, byte [cs:si]
    push ax
    push dword [OBJECT]
    call far [cs:reader] ; native reader consumes edge state/counter
    add sp, 6
    test al, 1          ; bit 0 = physically held; bit 1 alone = released edge
    jz .next
    mov eax, [cs:si+1]
    cmp byte [cs:si], 0x4b
    je .arrow
    cmp byte [cs:si], 0x4d
    jne .merge
.arrow:
    test byte [KEYS+0x38], 1
    jz .merge
    shl eax, 17        ; arrows+Alt: 4/8 -> 80000/100000
.merge:
    les bx, [bp+6]
    or [es:bx+0x28], eax
.next:
    add si, 5
    cmp si, bindings_end
    jb .poll
    jmp INPUT+0xdb     ; original nonmovement bindings at +0xdb
bindings:
    db 0x11
    dd 0x10
    db 0x1f
    dd 0x20
    db 0x1e
    dd 0x80000
    db 0x20
    dd 0x100000
    db 0x48
    dd 0x10
    db 0x50
    dd 0x20
    db 0x4b
    dd 4
    db 0x4d
    dd 8
bindings_end:
    times 195-($-$$) db 0x90
%elif SECTION = 2
ORG INPUT+0x268
    les bx, [bp+6]
    cmp word [es:bx+0x20], 0
    jne .done
    times 29-($-$$) db 0x90
    db 0x9a
    dw 0xffff,0       ; preserve native mouse-init relocation at INPUT+0x286
    les bx, [bp+6]
    or ax,ax
    jnz .store
    dec ax            ; -1 = unavailable, do not reset driver every frame
.store:
    mov [es:bx+0x20], ax
.done:
    jmp INPUT+0x2c1
    times 89-($-$$) db 0x90
%elif SECTION = 3
; Replace the original mixed turn/movement speed-halving block.
; EAX/EDX are caller-clobbered here. All other registers and SP unchanged.
    mov eax, [bp-0xe]
%macro cancel 1
    mov edx,eax
    and edx,%1
    cmp edx,%1
    jne %%keep
    and eax,~%1
%%keep:
%endmacro
    cancel 0x0c
    cancel 0x30
    cancel 0x180000
    mov [bp-0xe],eax
    times 75-($-$$) db 0x90
%elif SECTION = 4
ORG 0x174
; Direct angular displacement: 2/10 degree per horizontal driver mickey.
; MOUSE_X holds pending degrees modulo 360, not virtual arrow commands.
; Former Y accumulator holds the signed fractional numerator (-9..9).
; Signed 32-bit math safely covers every INT 33h signed-word sample.
    movsx eax, word [bp-8]
    shl eax, 1
    movsx edx, word [MOUSE_FRACTION]
    add eax, edx
    cdq
    mov ecx, 10
    idiv ecx
    mov [MOUSE_FRACTION], dx
    movsx edx, word [MOUSE_X]
    add eax, edx
    cdq
    mov ecx, 360
    idiv ecx
    mov [MOUSE_X], dx
    jmp 0x25e           ; buttons only; never emit mouse arrow bits 4/8
    times 142-($-$$) db 0x90
%elif SECTION = 5
ORG 0xdae+PLAYER_DELTA
    jmp short .end     ; normal translation flow skips the helper
angle_helper:
    les bx, [bp+6]     ; displaced instruction, keep native ES:BX contract
    cmp word [bp+2], HERO_RETURN ; base physics is also called by monster AI!
    jne .return
    xor ax, ax
    xchg ax, [MOUSE_X] ; only the human-player call may consume mouse motion
    neg ax
    add ax, [es:bx+0x32]
    test ax, ax
    jge .upper
    add ax, 360
.upper:
    cmp ax, 360
    jl .store
    sub ax, 360
.store:
    mov [es:bx+0x32], ax
.return:
    ret
    times 45-($-$$) db 0x90
.end:
%elif SECTION = 6
ORG 0xc3d+PLAYER_DELTA
    call 0xdb0+PLAYER_DELTA
%else
%error Unknown SECTION
%endif
