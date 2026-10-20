; Original WASDify code, MIT. BITS 16, DS = configuration data selector.
; Shared by the real-mode DOS setup and protected-mode game payload.
; Generated constants/tables are in settings-tables.inc (development only).

cfg_defaults:
    mov word [CFG_VIDEO], 0
    mov word [CFG_SENSITIVITY], 7
    mov word [CFG_VERSION], 0
    mov byte [CFG_VALID], 0
cfg_default_keys:
    push si
    push di
    push cx
    mov si, default_keys
    mov di, CFG_BINDINGS
    mov cx, ACTION_COUNT*2
.copy:
    mov al, [cs:si]
    mov [di], al
    inc si
    inc di
    loop .copy
    pop cx
    pop di
    pop si
    ret

; CS:DI literal prefix, DS:SI line. CF = mismatch; SI advanced on success.
cfg_match:
    push ax
    push si
.next:
    mov al, [cs:di]
    test al, al
    jz .yes
    cmp al, [si]
    jne .no
    inc di
    inc si
    jmp .next
.yes:
    add sp, 2
    pop ax
    clc
    ret
.no:
    pop si
    pop ax
    stc
    ret

; Decimal uint16. SI stops at comma/NUL; rejects overflow/empty input.
cfg_number:
    xor ax, ax
    xor cx, cx
.digit:
    mov bl, [si]
    cmp bl, '0'
    jb .end
    cmp bl, '9'
    ja .end
    sub bl, '0'
    xor bh, bh
    mov dx, 10
    mul dx
    test dx, dx
    jnz .bad
    add ax, bx
    jc .bad
    inc si
    inc cx
    jmp .digit
.end:
    jcxz .bad
    clc
    ret
.bad:
    stc
    ret

; AX scancode; 0 is the unassigned slot. CF = unsupported.
cfg_scan:
    test ax, ax
    jz .ok
    push cx
    push di
    mov di, allowed_scans
    mov cx, SCAN_COUNT
.find:
    cmp al, [cs:di]
    je .found
    inc di
    loop .find
    pop di
    pop cx
    stc
    ret
.found:
    pop di
    pop cx
.ok:
    clc
    ret

; CF = duplicate/unsupported binding. Never changes the user's bindings.
cfg_validate:
    xor si, si
.outer:
    xor ax, ax
    mov al, [CFG_BINDINGS+si]
    call cfg_scan
    jc .bad
    test al, al
    jz .next
    xor di, di
.inner:
    cmp di, si
    jae .next
    cmp al, [CFG_BINDINGS+di]
    je .bad
    inc di
    jmp .inner
.next:
    inc si
    cmp si, ACTION_COUNT*2
    jb .outer
    clc
    ret
.bad:
    stc
    ret

; CX file length <= FILE_LIMIT. The byte after the buffer is allocated.
cfg_parse:
    call cfg_defaults
    mov word [CFG_SENSITIVITY], 5
    mov byte [CFG_SECTION], 0
    mov byte [CFG_WORK], 0 ; invalid key field
    mov si, CFG_BUFFER
    mov di, si
    add di, cx
    mov byte [di], 0
    mov [CFG_WORK+2], di
.line:
    cmp si, [CFG_WORK+2]
    jae .finish
    mov di, CFG_LINE
    xor cx, cx
.character:
    cmp si, [CFG_WORK+2]
    jae .line_end
    lodsb
    cmp al, 10
    je .line_end
    cmp al, 13
    je .character
    inc cx
    cmp cx, LINE_LIMIT
    ja .character
    cmp al, ' '
    je .character
    cmp al, 9
    je .character
    cmp al, ';'
    je .comment
    cmp al, '#'
    je .comment
    cmp al, 'A'
    jb .store
    cmp al, 'Z'
    ja .store
    add al, 32
.store:
    mov [di], al
    inc di
    jmp .character
.comment:
    cmp si, [CFG_WORK+2]
    jae .line_end
    lodsb
    cmp al, 10
    jne .comment
.line_end:
    mov byte [di], 0
    push si
    cmp cx, LINE_LIMIT
    ja .skip
    mov si, CFG_LINE
    cmp byte [si], '['
    jne .field
    mov byte [CFG_SECTION], 0
    mov di, section_meta
    call cfg_match
    jc .video_section
    cmp byte [si], 0
    jne .skip
    mov byte [CFG_SECTION], 1
    jmp .skip
.video_section:
    mov di, section_video
    call cfg_match
    jc .mouse_section
    cmp byte [si], 0
    jne .skip
    mov byte [CFG_SECTION], 2
    jmp .skip
.mouse_section:
    mov di, section_mouse
    call cfg_match
    jc .keys_section
    cmp byte [si], 0
    jne .skip
    mov byte [CFG_SECTION], 3
    jmp .skip
.keys_section:
    mov di, section_keys
    call cfg_match
    jc .skip
    cmp byte [si], 0
    jne .skip
    mov byte [CFG_SECTION], 4
    jmp .skip
.field:
    cmp byte [CFG_SECTION], 1
    jne .video
    mov di, key_version
    call cfg_match
    jc .skip
    call cfg_number
    jc .invalid_version
    cmp byte [si], 0
    jne .invalid_version
    mov [CFG_VERSION], ax
    jmp .skip
.invalid_version:
    mov word [CFG_VERSION], 0
    jmp .skip
.video:
    cmp byte [CFG_SECTION], 2
    jne .mouse
    mov di, key_mode
    call cfg_match
    jc .skip
    mov word [CFG_VIDEO], 0
    cmp word [si], 0x312d ; -1
    jne .video_number
    cmp byte [si+2], 0
    jne .skip
    mov word [CFG_VIDEO], -1
    jmp .skip
.video_number:
    call cfg_number
    jc .skip
    cmp ax, 4
    ja .skip
    cmp byte [si], 0
    jne .skip
    mov [CFG_VIDEO], ax
    jmp .skip
.mouse:
    cmp byte [CFG_SECTION], 3
    jne .keys
    mov di, key_sensitivity
    call cfg_match
    jc .skip
    mov word [CFG_SENSITIVITY], 5
    call cfg_number
    jc .skip
    cmp ax, 9
    ja .skip
    cmp byte [si], 0
    jne .skip
    mov [CFG_SENSITIVITY], ax
    jmp .skip
.keys:
    cmp byte [CFG_SECTION], 4
    jne .skip
    xor bx, bx
.key_find:
    mov di, [cs:action_names+bx]
    call cfg_match
    jnc .key_value
    add bx, 2
    cmp bx, ACTION_COUNT*2
    jb .key_find
    jmp .skip
.key_value:
    mov [CFG_WORK+4], bx
    call cfg_number
    jc .bad_key
    cmp ax, 255
    ja .bad_key
    mov [CFG_WORK+6], al
    cmp byte [si], ','
    jne .bad_key
    inc si
    call cfg_number
    jc .bad_key
    cmp ax, 255
    ja .bad_key
    cmp byte [si], 0
    jne .bad_key
    mov bx, [CFG_WORK+4]
    mov ah, al
    mov al, [CFG_WORK+6]
    mov [CFG_BINDINGS+bx], ax
    jmp .skip
.bad_key:
    mov byte [CFG_WORK], 1
.skip:
    pop si
    jmp .line
.finish:
    cmp word [CFG_VERSION], 1
    jne .reject
    mov byte [CFG_VALID], 1
    cmp byte [CFG_WORK], 0
    jne .reset_keys
    call cfg_validate
    jnc .done
.reset_keys:
    call cfg_default_keys
.done:
    ret
.reject:
    jmp cfg_defaults

; CS:SI literal -> DS:DI output. NUL not copied.
cfg_text:
    mov al, [cs:si]
    inc si
    test al, al
    jz .done
    mov [di], al
    inc di
    jmp cfg_text
.done:
    ret
cfg_decimal:
    xor cx, cx
    mov bx, 10
.divide:
    xor dx, dx
    div bx
    push dx
    inc cx
    test ax, ax
    jnz .divide
.write:
    pop ax
    add al, '0'
    mov [di], al
    inc di
    loop .write
    ret
cfg_crlf:
    mov word [di], 0x0a0d
    add di, 2
    ret
cfg_serialize:
    mov di, CFG_OUTPUT
    mov si, ini_header
    call cfg_text
    mov ax, [CFG_VIDEO]
    test ax, ax
    jns .mode
    mov byte [di], '-'
    inc di
    neg ax
.mode:
    call cfg_decimal
    call cfg_crlf
    mov si, ini_mouse
    call cfg_text
    mov ax, [CFG_SENSITIVITY]
    call cfg_decimal
    call cfg_crlf
    mov si, ini_keys
    call cfg_text
    xor bp, bp
.key:
    mov si, [cs:action_names+bp]
    call cfg_text
    xor ax, ax
    mov al, [CFG_BINDINGS+bp]
    call cfg_decimal
    mov byte [di], ','
    inc di
    xor ax, ax
    mov al, [CFG_BINDINGS+bp+1]
    call cfg_decimal
    call cfg_crlf
    add bp, 2
    cmp bp, ACTION_COUNT*2
    jb .key
    mov cx, di
    sub cx, CFG_OUTPUT
    ret

; ES:SI executable path, DS:CFG_PATH output. CF on absent directory/overflow.
cfg_path:
    mov di, CFG_PATH
    xor bx, bx
    mov cx, 250
.copy:
    mov al, [es:si]
    inc si
    test al, al
    jz .end
    mov [di], al
    inc di
    cmp al, '\'
    je .separator
    cmp al, '/'
    jne .next
.separator:
    mov bx, di
.next:
    loop .copy
.bad:
    stc
    ret
.end:
    test bx, bx
    jz .bad
    mov di, bx
    mov si, ini_filename
    call cfg_text
    mov byte [di], 0
    clc
    ret

section_meta: db '[wasdify]',0
section_video: db '[video]',0
section_mouse: db '[mouse]',0
section_keys: db '[keys]',0
key_version: db 'version=',0
key_mode: db 'mode=',0
key_sensitivity: db 'sensitivity=',0
ini_header: db '[wasdify]',13,10,'version=1',13,10,'[video]',13,10,'mode=',0
ini_mouse: db '[mouse]',13,10,'sensitivity=',0
ini_keys: db '[keys]',13,10,0
ini_filename: db 'KREMLIN.INI',0
