; WASDify, MIT. TEST INPUT DRIVER ONLY; never included in a playable payload.
; No serializer, file installer or host API. Drive the exact F5/F9 keyboard
; state machine in a fresh DOSBox with webserver disabled, then report success
; only after the native loader's complete recapture and health check.
%define T_CLOCK 432
%define T_DOWN 436
%define T_STAGE 438
%define T_HEALTH 440
krs_native_offline_input:
    pushad
    push es
    push fs
    push gs
    cmp word [N_ERROR], 0
    jne .failure
    cmp word [N_ENABLED], 1
    jne .menus
    cmp word [N_BOUND+2], 0
    je .menus
    mov bx, [40]
    cmp word [ss:bx-0x3a], 6
    jne .menus
    cmp word [420], 0x0d75
    jne .done
    call native_segments
    les bx, [gs:bp+0x442]
    cmp word [T_STAGE], 0
    jne .saved
    mov ax, [es:bx+0x5e]
    mov [T_HEALTH], ax
    mov byte [ss:0x2194], 2
    mov word [T_STAGE], 1
    jmp .done
.saved:
    cmp word [T_STAGE], 1
    jne .loaded
    cmp word [N_SEQUENCE], 1
    jne .done
    cmp word [N_ACTION], 1
    jne .failure
    mov word [es:bx+0x5e], 1
    mov byte [ss:0x2198], 2
    mov word [T_STAGE], 2
    jmp .done
.loaded:
    cmp word [T_STAGE], 2
    jne .done
    cmp word [N_SEQUENCE], 2
    jne .done
    cmp word [N_ACTION], 2
    jne .failure
    mov ax, [es:bx+0x5e]
    cmp ax, [T_HEALTH]
    jne .failure
    mov si, .name
    call native_path_a
    mov ax, 2
    call native_io
    jne .failure
    mov ax, [RESULT]
    mov [HANDLE], ax
    mov si, .report
    mov di, BUFFER
    mov cx, .report_end-.report
.copy:
    mov al, [cs:si]
    mov [di], al
    inc si
    inc di
    loop .copy
    mov word [LENGTH], .report_end-.report
    mov ax, 4
    call native_io
    jne .failure
    cmp word [RESULT], .report_end-.report
    jne .failure
    mov ax, 6
    call native_io
    jne .failure
    mov ax, 7
    call native_io
    jne .failure
%ifdef KRS_NATIVE_OFFLINE_KEEP_OPEN
    mov word [T_STAGE], 3
    jmp .done
%else
    mov ax, 0x4c00
    int 0x21
    jmp $
%endif
.menus:
    call read_time
    mov edx, eax
    sub eax, [T_CLOCK]
    cmp eax, 2000
    jb .done
    mov [T_CLOCK], edx
    xor word [T_DOWN], 1
    cmp word [T_DOWN], 0
    je .release
    mov byte [ss:0x2171], 3
    inc word [ss:0x2152]
    mov byte [ss:0x2154], 0x1c
    jmp .done
.release:
    mov byte [ss:0x2171], 2
.done:
    pop gs
    pop fs
    pop es
    popad
    ret
.failure:
    mov ax, 0x4c01
    int 0x21
    jmp $
.name: db 'KRNTEST.OK',0
.report: db 'native F5/F9 and complete recapture passed',13,10
.report_end:
