; WASDify, MIT. NASM -f bin -DENG=0/1 -I build/settings/.
; Protected-mode 16-bit NE payload. All selectors use loader fixups.
BITS 16
ORG 0
%include "settings-tables.inc"
db 'WASDSET1'
dw startup, keyboard, mouse, cfg_parse, cfg_defaults, cfg_serialize
dw fixups, (fixups_end-fixups)/4
times 32-($-$$) db 0

load_config:
    mov ax, [cs:config_selector]
    mov ds, ax
    ret

; Called in main after native resolution defaults, before argv parsing.
; BP still denotes the native main frame. All incoming state preserved.
startup:
    mov word [ss:bp+MAIN_LOOP_LOCAL], 1 ; displaced native instruction
    pushfd
    pushad
    push ds
    push es
    push fs
    call load_config
    call cfg_defaults
    les bx, [ss:bp+8]       ; argv, protected far pointer
    les si, [es:bx]         ; argv[0], provided by the DOS RTL
    call cfg_path
    jc .done
    push ds
    push word 0             ; O_RDONLY
    push ds
    push word CFG_PATH
    push ss
    pop ds                  ; RTL globals belong to the game's automatic data
    call far [cs:rtl_open]
    add sp, 6
    pop ds
    cmp ax, -1
    je .done
    mov [CFG_WORK+16], ax
    push ds
    push word FILE_LIMIT+1
    push ds
    push word CFG_BUFFER
    push ax
    push ss
    pop ds
    call far [cs:rtl_read]
    add sp, 8
    pop ds
    mov [CFG_WORK+18], ax
    push ds
    push word [CFG_WORK+16]
    push ss
    pop ds
    call far [cs:rtl_close]
    add sp, 2
    pop ds
    mov cx, [CFG_WORK+18]
    cmp cx, FILE_LIMIT
    ja .done
    call cfg_parse
    cmp byte [CFG_VALID], 1
    jne .done
    ; An explicit -r or /r (case insensitive) overrides INI completely.
    mov cx, [ss:bp+6]
    dec cx
    jle .use_ini
    les di, [ss:bp+8]
    add di, 4
.argument:
    push es
    push di
    les bx, [es:di]
    mov al, [es:bx]
    cmp al, '-'
    je .switch
    cmp al, '/'
    jne .next
.switch:
    mov al, [es:bx+1]
    or al, 0x20
    cmp al, 'r'
    jne .next
    pop di
    pop es
    jmp .done
.next:
    pop di
    pop es
    add di, 4
    loop .argument
.use_ini:
    mov ax, [CFG_VIDEO]
    cmp ax, 1
    jl .store_mode
    ; Pointer-free VBE call, valid in the game's existing DOS extender.
    ; Test the actual BIOS return status; no ModeInfo pointer is passed.
    mov bx, ax
    dec bx
    shl bx, 1
    mov bx, [cs:video_modes+bx]
    push ax
    mov ax, 0x4f02
    int 0x10
    cmp ax, 0x004f
    pop ax
    je .store_mode
    xor ax, ax              ; VGA fallback for INI only
.store_mode:
    mov [ss:bp+MAIN_MODE_LOCAL], ax
    mov word [ss:bp+MAIN_TYPE_LOCAL], 2
.done:
    pop fs
    pop es
    pop ds
    popad
    popfd
    retf
video_modes: dw 0x100, 0x101, 0x103, 0x105

; Native keyboard frame already exists and command word is cleared.
keyboard:
    pushfd
    pushad
    push ds
    push es
    push fs
    call load_config
    les bx, [ss:bp+6]
    cmp word [es:bx+0x20], 0
    jne .start_poll
    push ds
    push ss
    pop ds
    call far [cs:mouse_init]
    pop ds
    test ax, ax
    jnz .mouse_available
    dec ax
.mouse_available:
    les bx, [ss:bp+6]
    mov [es:bx+0x20], ax
.start_poll:
    xor si, si
.poll:
    mov byte [CFG_STATES+si], 0
    mov di, si
    shl di, 1
    xor ax, ax
    mov al, [CFG_BINDINGS+di]
    call read_binding
    mov [CFG_STATES+si], al
    xor ax, ax
    mov al, [CFG_BINDINGS+di+1]
    call read_binding
    or [CFG_STATES+si], al
    inc si
    cmp si, ACTION_COUNT
    jb .poll
    xor edx, edx
    xor si, si
.commands:
    cmp byte [CFG_STATES+si], 0
    je .next_command
    mov di, si
    shl di, 2
    mov eax, [cs:action_masks+di]
    cmp si, 4
    je .turn
    cmp si, 5
    jne .merge
.turn:
    cmp byte [CFG_STATES+6], 0
    je .merge
    shl eax, 17
.merge:
    or edx, eax
.next_command:
    inc si
    cmp si, ACTION_COUNT
    jb .commands
    les bx, [ss:bp+6]
    mov [es:bx+0x28], edx
    cmp byte [CFG_STATES+10], 0
    je .map_state
    xor word [es:bx+0x8b], 1
    push ds
    push dword [ss:bp+6]
    push ss
    pop ds
    call far [cs:map_dirty]
    add sp, 4
    pop ds
.map_state:
    les bx, [ss:bp+6]
    cmp word [es:bx+0x8b], 0
    je .joystick
    or dword [es:bx+0x28], 0x2000000
.joystick:
    cmp byte [CFG_STATES+18], 0
    je .pause
    cmp word [es:bx+0x1e], 0
    jne .joystick_off
    push ds
    push ss
    pop ds
    call far [cs:joystick_init]
    pop ds
    les bx, [ss:bp+6]
    mov [es:bx+0x1e], ax
    jmp .pause
.joystick_off:
    mov word [es:bx+0x1e], 0
.pause:
    cmp byte [CFG_STATES+16], 0
    je .done
    push ds
    push word 0
    push ss
    push word PAUSE_TEXT
    push ss
    pop ds
    call far [cs:pause_game]
    add sp, 6
    pop ds
.done:
    pop fs
    pop es
    pop ds
    popad
    popfd
    jmp far [cs:resume_input] ; native joystick and mouse poll + epilogue

; SI action, AX physical key. Preserve SI/DI/BP and configuration DS.
read_binding:
    test ax, ax
    jz .done
    push di
    push si
    push ds
    push ax
    mov di, [cs:held_reader+2]
    mov ax, ss
    mov ds, ax
    pop ax
    push ax
    push dword [KEYBOARD_OBJECT]
    cmp byte [cs:action_edges+si], 0
    jne .edge
    call far [cs:held_reader]
    and ax, 1
    jmp .read
.edge:
    call far [cs:edge_reader]
.read:
    add sp, 6
    pop ds
    pop si
    pop di
.done:
    ret

; Native INT 33h sample is [BP-8]. Keep pending whole angle in game DS.
mouse:
    push fs
    push ax
    mov ax, [cs:config_selector]
    mov fs, ax
    pop ax
    movsx eax, word [ss:bp-8]
    imul eax, 6
    movsx edx, word [MOUSE_FRACTION]
    add eax, edx
    movzx ecx, word [fs:CFG_SENSITIVITY]
    neg ecx
    add ecx, 13
    imul ecx, 5
    cdq
    idiv ecx
    mov [MOUSE_FRACTION], dx
    movsx edx, word [MOUSE_X]
    add eax, edx
    cdq
    mov ecx, 360
    idiv ecx
    mov [MOUSE_X], dx
    pop fs
    jmp far [cs:resume_mouse]

%include "kremlin_config.inc"
%include "settings-table-data.inc"

; source offset and target token; build script supplies native target segments.
config_selector: dw 0xffff
rtl_open: dw 0xffff,0
rtl_read: dw 0xffff,0
rtl_close: dw 0xffff,0
held_reader: dw 0xffff,0
edge_reader: dw 0xffff,0
map_dirty: dw 0xffff,0
pause_game: dw 0xffff,0
joystick_init: dw 0xffff,0
resume_input: dw 0xffff,0
resume_mouse: dw 0xffff,0
mouse_init: dw 0xffff,0
fixups:
dw config_selector, 0
dw rtl_open, 1
dw rtl_read, 2
dw rtl_close, 3
dw held_reader, 4
dw edge_reader, 5
dw map_dirty, 6
dw pause_game, 7
dw joystick_init, 8
dw resume_input, 9
dw resume_mouse, 10
dw mouse_init, 11
fixups_end:
