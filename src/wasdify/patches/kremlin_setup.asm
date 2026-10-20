; WASDify DOS setup, original MIT code. NASM BITS 16, DOS 3+, VGA.
BITS 16
ORG 0
%include "settings-tables.inc"
db 'WASDSET1'
dw start, keyboard_irq, capture_scan, cfg_parse, cfg_defaults, cfg_serialize
dw save_ini, cfg_validate
times 32-($-$$) db 0
start:
    mov bx, es                  ; PSP supplied by DOS
    mov ax, cs
    add ax, DATA_PARAS
    mov ds, ax
    cld
    call cfg_defaults
    mov word [CFG_SENSITIVITY], 5
    mov word [CFG_WORK+32], 0   ; selection
    mov word [CFG_WORK+34], 0   ; key slot
    mov byte [CFG_WORK+36], 0   ; page
    mov es, bx
    mov ax, [es:0x2c]          ; DOS environment, real mode
    test ax, ax
    jz fatal_path
    mov es, ax
    xor si, si
    mov cx, 32760
.environment:
    cmp word [es:si], 0
    je .program
    inc si
    loop .environment
    jmp fatal_path
.program:
    add si, 4                  ; double NUL + program-name count
    call cfg_path
    jc fatal_path
    call load_ini
    mov ah, 0x0f
    int 0x10
    mov [CFG_WORK+38], al
    mov ax, 3
    int 0x10
%if LANG_RU
    push es
    push bp
    push cs
    pop es
    mov bp, russian_font
    mov ax, 0x1100
    mov bx, 0x1000
    mov cx, 48
    mov dx, 128
    int 0x10
    mov bp, russian_font_tail
    mov ax, 0x1100
    mov bx, 0x1000
    mov cx, 16
    mov dx, 224
    int 0x10
    pop bp
    pop es
%endif
main_loop:
    call draw
    xor ah, ah
    int 0x16
    cmp ah, 1
    je exit_setup
    cmp ah, 0x48
    je up
    cmp ah, 0x50
    je down
    cmp ah, 0x4b
    je left
    cmp ah, 0x4d
    je right
    cmp ah, 0x53
    je clear_binding
    cmp ah, 0x1c
    je enter
    jmp main_loop
up:
    cmp word [CFG_WORK+32], 0
    je main_loop
    dec word [CFG_WORK+32]
    jmp main_loop
down:
    mov ax, 5
    cmp byte [CFG_WORK+36], 0
    je .limit
    mov ax, ACTION_COUNT-1
.limit:
    cmp [CFG_WORK+32], ax
    jae main_loop
    inc word [CFG_WORK+32]
    jmp main_loop
left:
    mov dx, -1
    jmp adjust
right:
    mov dx, 1
adjust:
    cmp byte [CFG_WORK+36], 0
    jne .slot
    mov bx, [CFG_WORK+32]
    cmp bx, 1
    ja main_loop
    shl bx, 1
    add [CFG_VIDEO+bx], dx
    cmp bx, 0
    jne .sensitivity
    cmp word [CFG_VIDEO], -1
    jge .video_high
    mov word [CFG_VIDEO], 4
.video_high:
    cmp word [CFG_VIDEO], 4
    jle main_loop
    mov word [CFG_VIDEO], -1
    jmp main_loop
.sensitivity:
    cmp word [CFG_SENSITIVITY], 0
    jge .sens_high
    mov word [CFG_SENSITIVITY], 9
.sens_high:
    cmp word [CFG_SENSITIVITY], 9
    jle main_loop
    mov word [CFG_SENSITIVITY], 0
    jmp main_loop
.slot:
    xor word [CFG_WORK+34], 1
    jmp main_loop
clear_binding:
    cmp byte [CFG_WORK+36], 1
    jne main_loop
    mov bx, [CFG_WORK+32]
    shl bx, 1
    add bx, [CFG_WORK+34]
    mov byte [CFG_BINDINGS+bx], 0
    jmp main_loop
enter:
    cmp byte [CFG_WORK+36], 1
    je capture_binding
    mov ax, [CFG_WORK+32]
    cmp ax, 2
    jb right
    je .keys
    cmp ax, 3
    je .defaults
    cmp ax, 4
    je .save
    jmp exit_setup
.keys:
    mov byte [CFG_WORK+36], 1
    mov word [CFG_WORK+32], 0
    jmp main_loop
.defaults:
    call cfg_defaults
    mov word [CFG_SENSITIVITY], 5
    jmp main_loop
.save:
    call cfg_validate
    jc .conflict
    call save_ini
    jc .failed
    mov si, ui_saved
    jmp message
.conflict:
    mov si, ui_conflict
    jmp message
.failed:
    mov si, ui_write_error
message:
    call status_cursor
    call puts
    xor ah, ah
    int 0x16
    jmp main_loop
exit_setup:
    cmp byte [CFG_WORK+36], 1
    jne .exit
    mov byte [CFG_WORK+36], 0
    mov word [CFG_WORK+32], 2
    jmp main_loop
.exit:
    xor ah, ah
    mov al, [CFG_WORK+38]
    int 0x10
    mov ax, 0x4c00
    int 0x21
fatal_path:
    mov si, ui_path_error
    call puts
    mov ax, 0x4c01
    int 0x21

puts:                           ; CS:SI zero-terminated text, BIOS glyphs
    push ax
    push bx
    push si
.next:
    mov al, [cs:si]
    inc si
    test al, al
    jz .done
    mov ah, 0x0e
    mov bx, 7
    int 0x10
    jmp .next
.done:
    pop si
    pop bx
    pop ax
    ret
print_number:
    pushad
    mov di, CFG_OUTPUT
    call cfg_decimal
    mov byte [di], 0
    mov si, CFG_OUTPUT
.next:
    lodsb
    test al, al
    jz .done
    mov ah, 0x0e
    mov bx, 7
    int 0x10
    jmp .next
.done:
    popad
    ret
print_key:
    push bx
    push si
    xor ah, ah
    shl ax, 1
    mov bx, ax
    mov si, [cs:scan_labels+bx]
    call puts
    pop si
    pop bx
    ret
draw:
    pushad
    push es
    mov ax, 0xb800
    mov es, ax
    xor di, di
    mov ax, 0x0720
    mov cx, 2000
    rep stosw
    mov ah, 2
    xor bx, bx
    mov dx, 0x0103
    int 0x10
    mov si, ui_title
    call puts
    xor bp, bp
.row:
    mov dx, bp
    mov dh, dl
    add dh, 3                    ; rows 3..21, all 19 actions fit
    mov dl, 2
    call set_cursor
    mov si, ui_blank
    cmp bp, [CFG_WORK+32]
    jne .marker
    mov si, ui_marker
.marker:
    call puts
    mov bx, bp
    shl bx, 1
    cmp byte [CFG_WORK+36], 0
    jne .key_row
    mov si, [cs:main_labels+bx]
    call puts
    cmp bp, 0
    je .video
    cmp bp, 1
    jne .newline
    mov ax, [CFG_SENSITIVITY]
    call print_number
    jmp .newline
.video:
    mov bx, [CFG_VIDEO]
    inc bx
    shl bx, 1
    mov si, [cs:mode_labels+bx]
    call puts
    jmp .newline
.key_row:
    mov si, [cs:action_labels+bx]
    call puts
    mov al, [CFG_BINDINGS+bx]
    call print_key
    mov si, ui_separator
    call puts
    mov al, [CFG_BINDINGS+bx+1]
    call print_key
.newline:
    inc bp
    mov ax, 6
    cmp byte [CFG_WORK+36], 0
    je .limit
    mov ax, ACTION_COUNT
.limit:
    cmp bp, ax
    jb .row
    ; Apply attributes after glyph output: BIOS teletype preserves text cells.
    ; Keep the > marker as an additional, non-colour selection cue.
    mov ax, [CFG_WORK+32]
    add ax, 3
    mov di, 160
    mul di
    mov di, ax
    add di, 5                    ; row column 2, attribute byte
    mov cx, 76
.highlight:
    mov byte [es:di], 0x1f        ; bright white on blue, entire menu row
    add di, 2
    loop .highlight
    mov dx, 0x1702
    call set_cursor
    mov si, ui_help
    call puts
    mov dx, 0x1605
    call set_cursor
    cmp byte [CFG_WORK+36], 0
    je .done
    mov si, ui_slot
    call puts
    mov ax, [CFG_WORK+34]
    inc ax
    call print_number
.done:
    ; Hide the hardware cursor below the menu; restore via video mode on exit.
    mov ah, 1
    mov cx, 0x2000
    int 0x10
    pop es
    popad
    ret

set_cursor:                     ; DH row, DL column; preserve menu registers
    push ax
    push bx
    mov ah, 2
    xor bx, bx
    int 0x10
    pop bx
    pop ax
    ret

status_cursor:
    push dx
    mov dx, 0x1802
    call set_cursor
    pop dx
    ret

capture_binding:
    call status_cursor
    mov si, ui_capture
    call puts
    mov ax, 0x3509
    int 0x21
    mov [cs:old_irq], bx
    mov [cs:old_irq+2], es
    mov byte [cs:capture_scan], 0
    mov byte [cs:extended], 0
    mov byte [cs:pause_skip], 0
    push ds
    push cs
    pop ds
    mov dx, keyboard_irq
    mov ax, 0x2509
    int 0x21
    pop ds
.wait:
    sti
    hlt
    cmp byte [cs:capture_scan], 0
    je .wait
    push ds
    lds dx, [cs:old_irq]
    mov ax, 0x2509
    int 0x21
    pop ds
    xor ax, ax
    mov al, [cs:capture_scan]
    cmp al, 1
    je .flush
    call cfg_scan
    jc .flush
    mov bx, [CFG_WORK+32]
    shl bx, 1
    add bx, [CFG_WORK+34]
    mov [CFG_BINDINGS+bx], al
.flush:
    mov ah, 1
    int 0x16
    jz .done
    xor ah, ah
    int 0x16
    jmp .flush
.done:
    jmp main_loop
old_irq: dd 0
capture_scan: db 0
extended: db 0
pause_skip: db 0
keyboard_irq:
    push ax
    in al, 0x60
    cmp byte [cs:pause_skip], 0
    je .prefix
    dec byte [cs:pause_skip]
    jmp .ack
.prefix:
    cmp al, 0xe1
    jne .e0
    mov byte [cs:pause_skip], 5
    jmp .ack
.e0:
    cmp al, 0xe0
    jne .scan
    mov byte [cs:extended], 1
    jmp .ack
.scan:
    test al, 0x80
    jnz .reset
    cmp byte [cs:extended], 0
    je .store
    cmp al, 0x2a
    je .reset
    cmp al, 0x37
    je .reset
.store:
    cmp byte [cs:capture_scan], 0
    jne .reset
    mov [cs:capture_scan], al
.reset:
    mov byte [cs:extended], 0
.ack:
    in al, 0x61
    push ax
    or al, 0x80
    out 0x61, al
    pop ax
    out 0x61, al
    mov al, 0x20
    out 0x20, al
    pop ax
    iret

load_ini:
    mov dx, CFG_PATH
    mov ax, 0x3d00
    int 0x21
    jc .done
    mov bx, ax
    mov dx, CFG_BUFFER
    mov cx, FILE_LIMIT+1
    mov ah, 0x3f
    int 0x21
    pushf
    push ax
    mov ah, 0x3e
    int 0x21
    pop cx
    popf
    jc .done
    cmp cx, FILE_LIMIT
    ja .done
    call cfg_parse
    cmp byte [CFG_VALID], 0
    jne .done
    mov word [CFG_SENSITIVITY], 5
.done:
    ret

; Exclusive temporary file, byte verification, unique backup and rollback.
save_ini:
    call cfg_serialize
    mov [CFG_WORK+40], cx
    push ds
    pop es
    mov si, CFG_PATH
    mov di, CFG_TEMP_PATH
.path:
    lodsb
    stosb
    test al, al
    jnz .path
    mov word [di-4], 'TM'
    mov byte [di-2], 'P'
    mov dx, CFG_TEMP_PATH
    xor cx, cx
    mov ax, 0x5b00
    int 0x21
    jc .error
    mov bx, ax
    mov dx, CFG_OUTPUT
    mov cx, [CFG_WORK+40]
    mov ah, 0x40
    int 0x21
    jc .close_error
    cmp ax, [CFG_WORK+40]
    jne .close_error
    mov ah, 0x3e
    int 0x21
    jc .remove_error
    mov dx, CFG_TEMP_PATH
    mov ax, 0x3d00
    int 0x21
    jc .remove_error
    mov bx, ax
    mov dx, CFG_BUFFER
    mov cx, [CFG_WORK+40]
    mov ah, 0x3f
    int 0x21
    jc .close_error
    cmp ax, [CFG_WORK+40]
    jne .close_error
    mov ah, 0x3e
    int 0x21
    jc .remove_error
    mov si, CFG_OUTPUT
    mov di, CFG_BUFFER
    mov cx, [CFG_WORK+40]
    repe cmpsb
    jne .remove_error
    mov byte [CFG_WORK+42], 0
    mov dx, CFG_PATH
    mov ax, 0x4300
    int 0x21
    jnc .backup
    cmp ax, 2
    jne .remove_error
    jmp .replace
.backup:
    mov si, CFG_PATH
    mov di, CFG_BACKUP_PATH
.backup_path:
    lodsb
    stosb
    test al, al
    jnz .backup_path
    mov word [di-4], 'BA'
    mov byte [di-2], 'K'
    ; Replace the last four characters of the eight-character basename.
    ; KREMLIN.INI has seven chars: produce KRE0000.BAK, preserving 8.3.
    sub di, 9
    mov [CFG_WORK+44], di
    mov word [CFG_WORK+46], 0
.candidate:
    mov di, [CFG_WORK+44]
    mov ax, [CFG_WORK+46]
    mov bx, 1000
    xor dx, dx
    div bx
    add al, '0'
    stosb
    mov ax, dx
    mov bx, 100
    xor dx, dx
    div bx
    add al, '0'
    stosb
    mov ax, dx
    mov bx, 10
    xor dx, dx
    div bx
    add al, '0'
    stosb
    mov al, dl
    add al, '0'
    stosb
    mov dx, CFG_BACKUP_PATH
    mov ax, 0x4300
    int 0x21
    jc .available
    inc word [CFG_WORK+46]
    cmp word [CFG_WORK+46], 10000
    jb .candidate
    jmp .remove_error
.available:
    cmp ax, 2
    jne .remove_error
    mov dx, CFG_PATH
    mov di, CFG_BACKUP_PATH
    mov ah, 0x56
    int 0x21
    jc .remove_error
    mov byte [CFG_WORK+42], 1
.replace:
    mov dx, CFG_TEMP_PATH
    mov di, CFG_PATH
    mov ah, 0x56
    int 0x21
    jnc .success
    cmp byte [CFG_WORK+42], 0
    je .error
    mov dx, CFG_BACKUP_PATH
    mov di, CFG_PATH
    mov ah, 0x56
    int 0x21               ; On rollback failure, retain backup and temporary.
    jmp .error
.close_error:
    mov ah, 0x3e
    int 0x21
.remove_error:
    mov dx, CFG_TEMP_PATH
    mov ah, 0x41
    int 0x21
.error:
    stc
    ret
.success:
    clc
    ret

%include "kremlin_config.inc"
%include "settings-table-data.inc"
%include "settings-ui.inc"
