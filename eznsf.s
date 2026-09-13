
.segment "OAM"
.align 256
oam: .res 256

.segment "ZEROPAGE"
ptr:          .res 2
dst_ptr:      .res 2
nmi_count:    .res 1
gamepad:      .res 1

.segment "BSS"
pal:          .res 1
fps:          .res 1
frame:        .res 1
sec0:         .res 1
sec1:         .res 1
min0:         .res 1
min1:         .res 1
list_choose:  .res 1
list_scroll:  .res 1
list_rows_left: .res 1
list_row_idx: .res 1
track_choose: .res 1
play_through: .res 1
play_paused:  .res 1
play_len:     .res 2
play_secs:    .res 2
gamepad_last: .res 1
gamepad_new:  .res 1
temp:         .res 2
play_album:     .res 1
play_bank_f000: .res 1
debounce_a:   .res 1

PAD_A      = $01
PAD_B      = $02
PAD_SELECT = $04
PAD_START  = $08
PAD_U      = $10
PAD_D      = $20
PAD_L      = $40
PAD_R      = $80

.macro PPU_LATCH addr
	lda $2002
	lda #>(addr)
	sta $2006
	lda #<(addr)
	sta $2006
.endmacro

.define PPU_TILE(ax,ay) ($2000+(ay*32)+ax)

.macro PTR_LOAD addr
	lda #<(addr)
	sta ptr+0
	lda #>(addr)
	sta ptr+1
.endmacro

.macro STB addr, val
	lda val
	sta addr
.endmacro

.segment "CODE"
.include "enums.sh"
.include "tables.sh"

.proc mode_info
	lda #eScreen::INFO
	jsr load_screen
	INFO_ADDR = PPU_TILE(eCoord::INFO_X, eCoord::INFO_Y)
	lda #>INFO_ADDR
	sta temp+1
	sta $2006
	lda #<INFO_ADDR
	sta temp+0
	sta $2006
	PTR_LOAD dString::info
	:
		jsr ppu_string
		lda (ptr), Y
		beq :+
		jsr ppu_temp_line
		jmp :-
	:
	jsr oam_clear
@loop:
	jsr render_on
	jsr gamepad_poll
	lda gamepad_new
	and #(PAD_B)
	beq @loop
	jmp mode_list
.endproc

.proc list_sprite
	jsr oam_clear
	STB oam+(0*4)+1, #eConst::SPRITE_CHOOSE
	STB oam+(0*4)+2, #2
	STB oam+(0*4)+3, #(eCoord::TRACKS_TRACK_X-2)*8
	lda list_choose
	sec
	sbc list_scroll        ; row within the visible window, not the absolute index
	asl
	asl
	asl
	clc
	adc #(eCoord::TRACKS_TRACK_Y*8)-1
	sta oam+(0*4)+0
	rts
.endproc

; Blanks one full nametable row (32 tiles) at the current $2006 write
; position, advancing the PPU address by one row (32) in the process.
.proc ppu_clear_row
	lda #' '
	ldx #32
	:
		sta $2007
		dex
		bne :-
	rts
.endproc

; Draws one list row: ptr = string address, temp = row's PPU address.
; Clears the row first so a shorter string can't leave stale tiles behind
; from whatever was drawn there before (needed now that rows get reused
; as the list scrolls).
.proc list_draw_row
	lda temp+1
	sta $2006
	lda temp+0
	sta $2006
	jsr ppu_clear_row
	lda temp+1
	sta $2006
	lda temp+0
	sta $2006
	jmp ppu_string
.endproc

; Keeps list_scroll such that list_choose stays inside the visible window
; of eNSF::LISTROWS rows.
.proc list_clamp_scroll
	lda list_choose
	cmp list_scroll
	bcs @check_bottom
		sta list_scroll
		rts
	@check_bottom:
	sec
	sbc list_scroll
	cmp #eNSF::LISTROWS
	bcc @done
		lda list_choose
		sec
		sbc #(eNSF::LISTROWS-1)
		sta list_scroll
	@done:
	rts
.endproc

; Redraws the visible window of the list starting at list_scroll, then
; blanks any leftover rows below it (e.g. the last, shorter page).
.proc list_draw_body
	jsr render_off
	LIST_ADDR = PPU_TILE(eCoord::TRACKS_TRACK_X, eCoord::TRACKS_TRACK_Y)
	lda #<LIST_ADDR
	sta temp+0
	lda #>LIST_ADDR
	sta temp+1

	; rows_to_draw = min(eNSF::LISTROWS, eList::ENTRIES - list_scroll)
	lda #eList::ENTRIES
	sec
	sbc list_scroll
	cmp #eNSF::LISTROWS
	bcc @have_count
		lda #eNSF::LISTROWS
	@have_count:
	sta list_rows_left
	lda #0
	sta list_row_idx

	; NOTE: list_row_idx/list_scroll are kept in RAM rather than X/Y across
	; this loop, since list_draw_row (via ppu_clear_row/ppu_string) is free
	; to clobber both registers.
	@text_loop:
		lda list_row_idx
		cmp list_rows_left
		bcs @blank_check
		clc
		adc list_scroll
		asl
		tax
		lda dList::string_table+0, X
		sta ptr+0
		lda dList::string_table+1, X
		sta ptr+1
		jsr list_draw_row
		jsr ppu_temp_line
		inc list_row_idx
		jmp @text_loop
	@blank_check:
	lda list_row_idx
	cmp #eNSF::LISTROWS
	bcs @done
	@blank_loop:
		lda temp+1
		sta $2006
		lda temp+0
		sta $2006
		jsr ppu_clear_row
		jsr ppu_temp_line
		inc list_row_idx
		lda list_row_idx
		cmp #eNSF::LISTROWS
		bcc @blank_loop
	@done:
	rts
.endproc

.proc mode_list
	lda #0
	sta $4015
	sta $4011
	lda #eScreen::TRACKS
	jsr load_screen
	PPU_LATCH PPU_TILE(eCoord::TRACKS_TITLE_X, eCoord::TRACKS_TITLE_Y)
	PTR_LOAD dString::title
	jsr ppu_string
	PPU_LATCH PPU_TILE(eCoord::TRACKS_COPYRIGHT_X, eCoord::TRACKS_COPYRIGHT_Y)
	PTR_LOAD dString::copyright
	jsr ppu_string

	jsr list_clamp_scroll
	jsr list_draw_body
@loop:
	lda gamepad
	sta gamepad_last
	jsr list_sprite
	jsr render_on
	jsr gamepad_poll
	lda gamepad_new
	and #(PAD_L | PAD_U)
	beq :+
		lda list_choose
		beq @move_end
		dec list_choose
		jmp @after_move
	:
	lda gamepad_new
	and #(PAD_R | PAD_D)
	beq :+
		lda list_choose
		cmp #(eList::ENTRIES-1)
		bcs @move_end
		inc list_choose
		jmp @after_move
	:
	jmp @move_end
@after_move:
	jsr list_clamp_scroll
	jsr list_draw_body
@move_end:
	lda gamepad_new
	and #(PAD_A | PAD_START)
	beq @loop
		lda list_choose
		bne @not_play_all
			lda #0
			sta track_choose
			lda #1
			sta play_through
			jmp mode_play
		@not_play_all:
		cmp #(eList::ENTRIES-1)
		bne @not_info
			jmp mode_info
		@not_info:
		sec
		sbc #1
		sta track_choose
		lda #0
		sta play_through
		jmp mode_play
.endproc

.proc starfield_init
	ldx #24
	lda nmi_count
	bne @skip_zero
	lda #$89
@skip_zero:
	sta temp
@init_loop:
	jsr @rand
	sta oam+0, X
	lda #$0B
	sta oam+1, X
	lda #0
	sta oam+2, X
	jsr @rand
	sta oam+3, X
	inx
	inx
	inx
	inx
	bne @init_loop
	rts

@rand:
	lda temp
	ldy #8
@rand_loop:
	lsr
	bcc @skip_eor
	eor #$B4
@skip_eor:
	dey
	bne @rand_loop
	sta temp
	rts
.endproc

.proc play_sprite_init
	STB temp, #eCoord::PLAY_TIME_X+(0*8)
	ldx #0
	:
		lda #eCoord::PLAY_TIME_Y-1
		sta oam+0, X
		lda #2
		sta oam+2, X
		lda temp
		sta oam+3, X
		clc
		adc #8
		sta temp
		inx
		inx
		inx
		inx
		cpx #(6*4)
		bcc :-
	lda temp
	sta oam+(5*4)+3
	STB oam+(2*4)+1, #eConst::SPRITE_COLON
	jmp play_sprite
.endproc

.proc mode_play
	lda #0
	sta $4015
	sta $4011
	lda #eScreen::PLAY
	jsr load_screen

	PPU_LATCH PPU_TILE(eCoord::PLAY_TRACK_X, eCoord::PLAY_TRACK_Y - 2)
	ldx track_choose
	lda dTrack::album_table, X
	asl
	tax
	lda dNSF::artist_table+0, X
	sta ptr+0
	lda dNSF::artist_table+1, X
	sta ptr+1
	jsr ppu_string

	PPU_LATCH PPU_TILE(eCoord::PLAY_TRACK_X, eCoord::PLAY_TRACK_Y)
	lda track_choose
	asl
	tax
	lda dTrack::string_table+0, X
	sta ptr+0
	lda dTrack::string_table+1, X
	sta ptr+1
	jsr ppu_string

	PPU_LATCH PPU_TILE(eCoord::PLAY_TRACK_X, eCoord::PLAY_TRACK_Y + 2)
	lda track_choose
	asl
	tax
	lda dTrack::copy_table+0, X
	sta ptr+0
	lda dTrack::copy_table+1, X
	sta ptr+1
	jsr ppu_string
	jsr play_sprite_init

	jsr starfield_init

	jsr play_init
	jmp ramcode_pin_and_enter_play_loop
.endproc

.proc play_init
	lda gamepad
	pha
	lda #0
	tax
	:
		sta $00, X
		inx
		cpx #$FA
		bcc :-
	pla
	sta gamepad
	lda #0
	tax
	:
		sta $0200, X
		sta $0300, X
		inx
		bne :-
	lda #0
	tax
	:
		sta $4000, X
		inx
		cpx #$14
		bcc :-
	STB $4015, #$0F
	STB $4017, #$40
	lda #0
	sta frame
	sta sec0
	sta sec1
	sta min0
	sta min1
	sta play_paused
	sta play_secs+0
	sta play_secs+1
	sta debounce_a
	lda track_choose
	asl
	tax
	lda dTrack::length_table+0, X
	sta play_len+0
	lda dTrack::length_table+1, X
	sta play_len+1
	ldx track_choose
	lda dTrack::album_table, X
	sta play_album
	asl
	tay
	lda dNSF::init_table+0, Y
	sta nsf_call_init_target+1
	lda dNSF::init_table+1, Y
	sta nsf_call_init_target+2
	lda play_album
	asl
	tay
	lda dNSF::play_table+0, Y
	sta nsf_call_play_target+1
	lda dNSF::play_table+1, Y
	sta nsf_call_play_target+2
	ldx play_album
	lda dNSF::bankf000_table, X
	sta play_bank_f000
	ldx track_choose
	lda dTrack::song_table, X
	jmp ramcode_nsf_init
.endproc

.segment "RAMCODE"

.if (MAPPER = 31)
ramcode_reset:
	lda #$FF
	sta $5FFF
	jmp vec_reset
ramcode_nmi:
	inc nmi_count
ramcode_irq:
	rti
.endif

ramcode_nsf_init:
	.if (::MAPPER = 31)
		pha
		lda play_album
		asl
		asl
		asl
		clc
		adc #<dNSF::bank_table
		sta ptr+0
		lda #>dNSF::bank_table
		adc #0
		sta ptr+1
		ldy #0
		:
			lda (ptr), Y
			sta $5FF8, Y
			iny
			cpy #8
			bcc :-
		pla
	.endif
	ldx pal
	ldy #0
nsf_call_init_target:
	jsr $FFFF
	jmp ramcode_return

ramcode_nsf_play:
	lda #0
	tax
	tay
nsf_call_play_target:
	jsr $FFFF
	rts

.proc ramcode_return
	.if (::MAPPER = 31)
		lda #$FF
		sta $5FFF
	.endif
	rts
.endproc

ramcode_pin_and_enter_play_loop:
	.if (::MAPPER = 31)
		lda play_bank_f000
		sta $5FFF
	.endif
	jmp play_loop

.segment "PLAYCODE"

.proc starfield_update
	ldx #24
@update_loop:
	txa
	and #%00011100
	lsr
	lsr
	sta temp
	lda oam+3, X
	sec
	sbc temp
	sbc #1
	sta oam+3, X
	inx
	inx
	inx
	inx
	bne @update_loop
	rts
.endproc

.proc wait_nmi
	lda #%10000000
	sta $2000
	lda nmi_count
	:
		cmp nmi_count
		beq :-
	rts
.endproc

.proc render_off
	jsr wait_nmi
	lda #0
	sta $2001
	rts
.endproc

.proc render_on
	jsr wait_nmi
	lda #0
	sta $2003
	lda #>oam
	sta $4014
	lda $2002
	lda #0
	sta $2005
	sta $2005
	lda #%00011110
	sta $2001
	rts
.endproc

.proc gamepad_poll
	lda gamepad
	sta gamepad_last
	lda #1
	sta $4016
	lda #0
	sta $4016
	lda #%10000000
	sta gamepad
	:
		lda $4016
		and #%00000001
		cmp #%00000001
		ror gamepad
	bcc :-
@reread:
	lda gamepad
	pha
	lda #1
	sta $4016
	lda #0
	sta $4016
	lda #%10000000
	sta gamepad
	:
		lda $4016
		and #%00000001
		cmp #%00000001
		ror gamepad
	bcc :-
	pla
	cmp gamepad
	bne @reread
	lda gamepad_last
	eor gamepad
	and gamepad
	sta gamepad_new
	rts
.endproc

.proc play_sprite
	lda min1
	clc
	adc #eConst::SPRITE_ZERO
	sta oam+(0*4)+1
	lda min0
	clc
	adc #eConst::SPRITE_ZERO
	sta oam+(1*4)+1
	lda sec1
	clc
	adc #eConst::SPRITE_ZERO
	sta oam+(3*4)+1
	lda sec0
	clc
	adc #eConst::SPRITE_ZERO
	sta oam+(4*4)+1
	lda play_paused
	cmp #2
	bcc :+
		lda #eConst::SPRITE_STOP
		jmp @indicator
	:
	cmp #1
	bcc :+
		lda #eConst::SPRITE_PAUSE
		jmp @indicator
	:
	lda play_through
	beq :+
		lda #eConst::SPRITE_PLAY_ALL
		jmp @indicator
	:
		lda #eConst::SPRITE_PLAY
	@indicator:
	sta oam+(5*4)+1
	rts
.endproc

.proc play_pause
	cmp play_paused
	bne :+
		rts
	:
	cmp #0
	bne @pause
@unpause:
	lda #$0F
	sta $4015
	lda #0
	sta play_paused
	rts
@pause:
	lda #0
	sta $4015
	lda #1
	sta play_paused
	rts
.endproc

.proc play_play
	lda play_paused
	beq :+
		rts
	:
	jsr ramcode_nsf_play
	inc frame
	lda frame
	cmp fps
	bcc @second_end
		lda #0
		sta frame
		inc sec0
		lda sec0
		cmp #10
		bcc :+
		lda #0
		sta sec0
		inc sec1
		lda sec1
		cmp #6
		bcc :+
		lda #0
		sta sec1
		inc min0
		lda min0
		cmp #10
		bcc :+
		lda #0
		sta min0
		inc min1
		lda min1
		cmp #10
		bcc :+
		lda #9
		sta min1
		sta min0
		sta sec0
		lda #5
		sta sec1
		:
		inc play_secs+0
		bne :+
			inc play_secs+1
		:
		lda play_secs+0
		cmp play_len+0
		lda play_secs+1
		sbc play_len+1
		bcc @second_end
		lda play_len+0
		sta play_secs+0
		lda play_len+1
		sta play_secs+1
		lda #2
		sta play_paused
		lda #0
		sta $4015
	@second_end:
	rts
.endproc

.proc play_loop
@loop:
	jsr play_sprite

	jsr starfield_update

	jsr render_on
	jsr play_play
	jsr gamepad_poll
	lda play_paused
	cmp #2
	bcs @track_finished
@playing:
	lda gamepad_new
	and #(PAD_START)
	beq :+
		lda play_paused
		eor #1
		jsr play_pause
	:
	lda debounce_a
	beq @check_a
		dec debounce_a
		jmp @skip_a
	@check_a:
	lda gamepad_new
	and #(PAD_A)
	beq @skip_a
		lda play_through
		eor #1
		sta play_through
		lda #15
		sta debounce_a
	@skip_a:
	lda gamepad_new
	and #(PAD_B)
	beq :+
		jmp @to_list
	:
	lda gamepad_new
	and #(PAD_L | PAD_U)
	beq :+
		lda track_choose
		beq :+
		dec track_choose
		jmp @to_play
	:
	lda gamepad_new
	and #(PAD_R | PAD_D)
	beq :+
		lda track_choose
		cmp #(eNSF::TRACKS-1)
		bcs :+
		inc track_choose
		jmp @to_play
	:
	jmp @loop
@track_finished:
	lda play_through
	beq @to_play
		lda track_choose
		cmp #(eNSF::TRACKS-1)
		bcs @wrap
		inc track_choose
		jmp @to_play
	@wrap:
		lda #0
		sta track_choose
@to_list:
	.if (::MAPPER = 31)
		lda #$FF
		sta $5FFF
	.endif
	jmp mode_list
@to_play:
	.if (::MAPPER = 31)
		lda #$FF
		sta $5FFF
	.endif
	jmp mode_play
.endproc

.segment "CODE"

.import __RAMCODE_SIZE__
.import __RAMCODE_LOAD__
.import __RAMCODE_RUN__

.proc load_ramcode
	.assert (__RAMCODE_SIZE__ < 256), error, "RAMCODE segment is too large."
	.assert (__RAMCODE_SIZE__ > 0), error, "RAMCODE segment empty."
	ldx #0
	:
		lda __RAMCODE_LOAD__, X
		sta __RAMCODE_RUN__, X
		inx
		cpx #<__RAMCODE_SIZE__
		bcc :-
	rts
.endproc

.import __PLAYCODE_SIZE__
.import __PLAYCODE_LOAD__
.import __PLAYCODE_RUN__

.proc load_playcode
	.assert (__PLAYCODE_SIZE__ > 0), error, "PLAYCODE segment empty."
	lda #<__PLAYCODE_LOAD__
	sta ptr+0
	lda #>__PLAYCODE_LOAD__
	sta ptr+1
	lda #<__PLAYCODE_RUN__
	sta dst_ptr+0
	lda #>__PLAYCODE_RUN__
	sta dst_ptr+1
	ldx #>__PLAYCODE_SIZE__
	ldy #0
@full_pages:
	cpx #0
	beq @partial
		:
			lda (ptr), Y
			sta (dst_ptr), Y
			iny
			bne :-
		inc ptr+1
		inc dst_ptr+1
		dex
		jmp @full_pages
@partial:
	ldy #0
	cpy #<__PLAYCODE_SIZE__
	beq @done
	:
		lda (ptr), Y
		sta (dst_ptr), Y
		iny
		cpy #<__PLAYCODE_SIZE__
		bcc :-
@done:
	rts
.endproc

.proc main
	PPU_LATCH $2400
	ldy #4
	lda #0
	tax
	:
		sta $2007
		inx
		bne :-
		dey
		bne :-
	jmp mode_list
.endproc

.proc read_ptr
	lda (ptr), Y
	inc ptr+0
	bne :+
		inc ptr+1
	:
	cmp #0
	rts
.endproc

.proc ppu_unpack
	asl
	tax
	lda dPPU::data_table+0, X
	sta ptr+0
	lda dPPU::data_table+1, X
	sta ptr+1
	ldy #0
	@rle_loop:
		jsr read_ptr
		beq @compressed
	@uncompressed:
		tax
		:
			jsr read_ptr
			sta $2007
			dex
			bne :-
		jmp @rle_loop
	@compressed:
		jsr read_ptr
		beq @finished
		tax
		jsr read_ptr
		:
			sta $2007
			dex
			bne :-
		jmp @rle_loop
	@finished:
		rts
.endproc

.proc ppu_string
	ldy #0
	:
		jsr read_ptr
		beq :+
		cmp #13
		beq :+
		sta $2007
		jmp :-
	:
	rts
.endproc

.proc load_screen
	sta temp
	jsr load_ppu_banks
	jsr render_off
	PPU_LATCH $3F00
	ldx temp
	lda dScreen::pal0_table, X
	jsr ppu_unpack
	ldx temp
	lda dScreen::pal1_table, X
	jsr ppu_unpack
	PPU_LATCH $0000
	ldx temp
	lda dScreen::chr0_table, X
	jsr ppu_unpack
	ldx temp
	lda dScreen::chr1_table, X
	jsr ppu_unpack
	PPU_LATCH $2000
	ldx temp
	lda dScreen::name_table, X
	jsr ppu_unpack
	rts
.endproc

.proc oam_clear
	lda #$FF
	ldx #0
	:
		sta oam, X
		inx
		inx
		inx
		inx
		bne :-
	rts
.endproc

.proc ppu_temp_line
	lda temp+0
	clc
	adc #<32
	sta temp+0
	lda temp+1
	adc #>32
	sta temp+1
	sta $2006
	lda temp+0
	sta $2006
	rts
.endproc

.proc vec_reset
	sei
	cld
	ldx #$40
	stx $4017
	ldx #$ff
	txs
	ldx #$00
	stx $2000
	stx $2001
	stx $4010
	stx $4015
	bit $2002
	:
		bit $2002
		bpl :-
	ldx #$00
	:
		lda #$00
		sta $0000, X
		sta $0100, X
		sta $0200, X
		sta $0300, X
		sta $0400, X
		sta $0500, X
		sta $0600, X
		sta $0700, X
		inx
		bne :-
	:
		bit $2002
		bpl :-
	jsr load_ramcode
	jsr load_playcode
	lda $2002
	lda #%10000000
	sta $2000
	jsr detect_region
	bne :+
		lda #60
		sta fps
		lda #0
		jmp :++
	:
		lda #50
		sta fps
		lda #1
	:
	sta pal
	jmp main
.endproc

vec_nmi:
	inc nmi_count
vec_irq:
	rti

.if (MAPPER = 31)

dPPU::data_base = $8000
INES_CHR = 0

.segment "CODE"
.proc load_ppu_banks
	ldx #eNSF::BANKS
	stx $5FF8
	inx
	stx $5FF9
	inx
	stx $5FFA
	inx
	stx $5FFB
	inx
	stx $5FFC
	inx
	stx $5FFD
	inx
	stx $5FFE
	rts
.endproc

.segment "NSF_F000"
.incbin NSF_F000

.segment "NSF_VECTORS"
.addr ramcode_nmi
.addr ramcode_reset
.addr ramcode_irq

.else

.segment "CODE"
ppu_nrom:
	.incbin PPU_NROM_BIN
	dPPU::data_base = ppu_nrom

.segment "TILES"
	.incbin NROM_CHR0
	.incbin NROM_CHR1
	INES_CHR = 1

.segment "NSF"
nsf_nrom:
	.incbin NSF_NROM_BIN
	.assert (nsf_nrom = $8000), error, "nsf_nrom.bin loaded at the wrong address?"

.segment "CODE"
.proc load_ppu_banks
	rts
.endproc

.endif

.if (eNSF::REGION & 2)
	.segment "ALIGN"
	.proc detect_region
		.align 32
		ldx #0
		ldy #0
		lda nmi_count
		@wait1:
			cmp nmi_count
			beq @wait1
			lda nmi_count
		@wait2:
			inx
			bne :+
				iny
			:
			cmp nmi_count
			beq @wait2
		tya
		sec
		sbc #10
		rts
	.endproc
.else
	.segment "CODE"
	.proc detect_region
		lda #(eNSF::REGION & 1)
		rts
	.endproc
.endif

.segment "VECTORS"
.addr vec_nmi
.addr vec_reset
.addr vec_irq

.segment "HEADER"
INES_MAPPER = MAPPER
INES_MIRROR = 1
INES_SRAM   = 0
.byte 'N', 'E', 'S', $1A
.byte BANKS / 4
.byte INES_CHR
.byte INES_MIRROR | (INES_SRAM << 1) | ((INES_MAPPER & $f) << 4)
.byte (INES_MAPPER & %11110000)
.byte $0, $0, $0, $0, $0, $0, $0, $0

