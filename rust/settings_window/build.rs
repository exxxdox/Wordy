use super::*;

impl SettingsWindow {
    pub(super) fn build(&mut self) -> io::Result<()> {
        use Field::*;
        use Kind::*;
        use Page::{Audio, Local, Rolling, Tts};

        self.add(None, None, Column, "设置", [48, 19, 300, 25])?;
        for (field, caption, x) in [
            (Minimize, "最小化", 1000),
            (Maximize, "最大化", 1034),
            (TitleClose, "关闭", 1068),
        ] {
            self.add(Some(field), None, Button, caption, [x, 20, 28, 28])?;
        }
        let tab_geometry = [(48, 86), (138, 86), (228, 112), (344, 86), (434, 86)];
        for ((page, field, caption, heading, description), (x, width)) in
            PAGES.into_iter().zip(tab_geometry)
        {
            self.add(Some(field), None, Button, caption, [x, 56, width, 30])?;
            self.add(None, Some(page), Heading, heading, [48, 99, 972, 43])?;
            self.add(None, Some(page), Muted, description, [48, 137, 972, 24])?;
        }

        for (page, field, kind, caption, bounds) in [
            (Local, None, Column, "快捷键与播放", [48, 171, 440, 38]),
            (Local, None, Label, "全局快捷键", [48, 222, 300, 22]),
            (Local, Some(HotkeyValue), Current, "", [48, 249, 335, 32]),
            (
                Local,
                Some(Hotkey),
                Button,
                "录制快捷键",
                [397, 250, 91, 32],
            ),
            (
                Local,
                None,
                Muted,
                "点击录制后按下新的快捷键组合",
                [48, 282, 440, 24],
            ),
            (
                Local,
                Some(HoldKeyEnabled),
                Check,
                "播放时自动按住某键",
                [48, 310, 154, 32],
            ),
            (Local, Some(HoldKeyValue), Current, "", [201, 310, 194, 32]),
            (Local, Some(HoldKey), Button, "录制按键", [410, 310, 78, 32]),
            (
                Local,
                None,
                Muted,
                "开启后，播放语音前自动按下该键，结束后松开。",
                [48, 342, 440, 24],
            ),
            (Local, None, Label, "朗读音量", [48, 391, 160, 24]),
            (Local, Some(VolumeValue), Value, "", [395, 396, 93, 17]),
            (Local, Some(Volume), Slider, "", [40, 414, 456, 28]),
            (
                Local,
                None,
                Muted,
                "范围 0.50x - 2.00x，默认 1.00x",
                [48, 438, 440, 24],
            ),
            (Local, None, Column, "界面与显示", [578, 171, 440, 38]),
            (Local, None, Label, "界面字体", [578, 222, 440, 22]),
            (Local, Some(UiFont), Combo, "", [578, 250, 440, 242]),
            (Local, Some(FontHint), Muted, "", [578, 282, 440, 24]),
            (Local, None, Label, "输入框提示文字", [578, 314, 440, 24]),
            (Local, Some(Placeholder), Edit, "", [578, 343, 440, 32]),
            (
                Local,
                None,
                Label,
                "输入框背景不透明度",
                [578, 391, 240, 24],
            ),
            (Local, Some(OpacityValue), Value, "", [937, 396, 81, 17]),
            (Local, Some(Opacity), Slider, "", [570, 414, 456, 28]),
            (
                Local,
                None,
                Muted,
                "30% 更透明，100% 完全不透明；文字保持清晰。",
                [578, 438, 440, 24],
            ),
            (Local, None, Label, "输入栏位置", [578, 469, 440, 22]),
            (
                Local,
                Some(FixedCenter),
                Check,
                "固定出现在屏幕中心",
                [578, 491, 440, 25],
            ),
            (
                Local,
                None,
                Muted,
                "取消勾选后，可拖动输入窗口；松开鼠标后自动记住位置。",
                [578, 515, 440, 24],
            ),
            (Local, None, Label, "日志等级", [578, 549, 440, 22]),
            (Local, Some(LogLevel), Combo, "", [578, 575, 440, 180]),
            (
                Local,
                None,
                Muted,
                "设置日志记录的详细程度。",
                [578, 607, 440, 24],
            ),
            (Audio, None, Column, "播放与返听", [48, 171, 440, 38]),
            (Audio, None, Label, "主播放输出", [48, 222, 440, 22]),
            (
                Audio,
                Some(MainPlayback),
                Check,
                "启用主播放输出",
                [48, 246, 440, 24],
            ),
            (Audio, Some(CableStatus), Muted, "", [48, 272, 440, 28]),
            (
                Audio,
                Some(Download),
                Button,
                "下载并安装 VB-CABLE",
                [48, 304, 169, 30],
            ),
            (Audio, None, Label, "返听", [48, 340, 440, 20]),
            (Audio, Some(Sidetone), Check, "启用返听", [48, 362, 440, 24]),
            (Audio, None, Label, "返听播放设备", [48, 388, 440, 20]),
            (Audio, Some(Output), Combo, "", [48, 412, 440, 230]),
            (Audio, Some(OutputStatus), Muted, "", [48, 445, 440, 24]),
            (Audio, None, Column, "麦克风与侦听", [578, 171, 440, 38]),
            (Audio, None, Label, "音频侦听", [578, 222, 440, 22]),
            (
                Audio,
                None,
                Muted,
                "通过 Windows 将所选麦克风送至 CABLE Input。",
                [578, 244, 440, 22],
            ),
            (Audio, None, Label, "侦听输出设备", [578, 278, 440, 22]),
            (
                Audio,
                Some(VirtualMic),
                DisabledField,
                "",
                [578, 305, 440, 32],
            ),
            (
                Audio,
                Some(Listen),
                Check,
                "启用音频侦听",
                [578, 341, 440, 25],
            ),
            (Audio, None, Label, "侦听麦克风", [578, 366, 440, 22]),
            (Audio, Some(Mic), Combo, "", [578, 392, 440, 230]),
            (Audio, Some(MicStatus), Muted, "", [578, 424, 440, 24]),
            (Tts, None, Column, "生成方式", [48, 171, 395, 38]),
            (Tts, None, Label, "TTS 服务商", [48, 222, 395, 22]),
            (Tts, Some(Provider), Combo, "", [48, 250, 395, 160]),
            (Tts, None, Label, "生成模式", [48, 302, 395, 22]),
            (Tts, Some(Backend), Combo, "", [48, 330, 395, 160]),
            (
                Tts,
                None,
                Muted,
                "Bytes：完整生成后播放。Streaming：边生成边播放。",
                [48, 362, 395, 24],
            ),
            (Tts, None, Column, "密钥与音色", [495, 171, 577, 38]),
            (Tts, None, Label, "API Key", [495, 222, 577, 22]),
            (Tts, Some(KeyStatus), Muted, "", [495, 244, 576, 22]),
            (Tts, Some(Key), Password, "", [495, 271, 385, 32]),
            (Tts, Some(SaveKey), Button, "保存", [888, 271, 88, 32]),
            (Tts, Some(DeleteKey), Button, "删除", [984, 271, 88, 32]),
            (
                Tts,
                None,
                Muted,
                "密钥保存在 Windows 凭据管理器中。",
                [495, 304, 577, 24],
            ),
            (Tts, None, Label, "音色选择", [495, 330, 577, 22]),
            (
                Tts,
                None,
                Muted,
                "选择当前用于朗读的音色。",
                [495, 352, 577, 22],
            ),
            (Tts, Some(Voices), Combo, "", [495, 376, 481, 230]),
            (
                Tts,
                Some(RefreshVoices),
                Button,
                "刷新音色",
                [984, 376, 88, 32],
            ),
            (
                Tts,
                Some(DeleteVoice),
                Button,
                "删除音色",
                [984, 376, 88, 32],
            ),
            (Tts, Some(VoiceHint), Muted, "", [495, 437, 577, 22]),
            (Tts, None, Muted, "Speaker ID", [495, 463, 124, 32]),
            (Tts, Some(VoiceId), Edit, "", [624, 465, 448, 32]),
            (Tts, None, Muted, "名称", [495, 505, 124, 32]),
            (Tts, Some(VoiceName), Edit, "", [624, 505, 448, 32]),
            (
                Tts,
                Some(SaveVoice),
                Button,
                "添加 / 保存音色",
                [892, 545, 180, 32],
            ),
            (
                Page::Wheels,
                Some(Wheels),
                Button,
                "编辑聊天轮盘",
                [48, 181, 200, 32],
            ),
            (Rolling, None, Column, "录制设置", [48, 171, 440, 38]),
            (
                Rolling,
                Some(RollingHotkeyValue),
                Current,
                "",
                [48, 222, 333, 32],
            ),
            (
                Rolling,
                Some(RollingHotkey),
                Button,
                "录制快捷键",
                [397, 222, 91, 32],
            ),
            (
                Rolling,
                None,
                Muted,
                "点击录制后按下新的快捷键组合",
                [48, 256, 440, 24],
            ),
            (Rolling, None, Label, "保留时长", [48, 289, 84, 34]),
            (
                Rolling,
                Some(RollingSeconds),
                Number,
                "",
                [132, 289, 116, 34],
            ),
            (Rolling, None, Muted, "秒（5–300）", [258, 289, 100, 34]),
            (Rolling, None, Column, "录制与保存", [578, 171, 440, 38]),
            (Rolling, None, Label, "开始和结束录制", [578, 222, 440, 22]),
            (
                Rolling,
                None,
                Muted,
                "按一次快捷键开始录制，再按一次打开保存窗口。",
                [578, 247, 440, 24],
            ),
            (Rolling, None, Label, "保存到轮盘", [578, 290, 440, 22]),
            (
                Rolling,
                None,
                Muted,
                "拖选片段，试听后选择轮盘槽位并保存。",
                [578, 315, 440, 24],
            ),
        ] {
            self.add(field, Some(page), kind, caption, bounds)?;
        }
        for (field, kind, caption, bounds) in [
            (None, Muted, "设置自动保存", [48, -56, 105, 32]),
            (Some(OpenLogs), Button, "查看日志", [157, -56, 92, 32]),
            (Some(Export), Button, "导出备份", [812, -56, 78, 32]),
            (Some(Import), Button, "导入备份", [898, -56, 78, 32]),
            (Some(Close), Button, "关闭", [983, -56, 88, 32]),
        ] {
            self.add(field, None, kind, caption, bounds)?;
        }
        send(self.control(Volume), TBM_SETRANGEMAX, 1, 30);
        send(self.control(Opacity), TBM_SETRANGEMAX, 1, 14);
        send(self.control(RollingSeconds), EM_SETLIMITTEXT, 3, 0);
        send(self.control(Key), EM_SETLIMITTEXT, 1280, 0);
        self.show_page();
        Ok(())
    }

    fn add(
        &mut self,
        field: Option<Field>,
        page: Option<Page>,
        kind: Kind,
        caption: &str,
        bounds: Bounds,
    ) -> io::Result<()> {
        let (class, style, ex) = kind.window();
        let caption = wide(caption);
        let id = field.map_or(0, |field| field as usize);
        let hwnd = unsafe {
            CreateWindowExW(
                ex,
                class,
                caption.as_ptr(),
                WS_CHILD | WS_CLIPSIBLINGS | style,
                scaled(bounds[0], self.scale),
                scaled(bounds[1], self.scale),
                scaled(bounds[2], self.scale),
                scaled(bounds[3], self.scale),
                self.hwnd,
                id as HMENU,
                self.instance,
                ptr::null(),
            )
        };
        if hwnd.is_null() {
            return Err(io::Error::last_os_error());
        }
        apply_font(hwnd, kind, &self.fonts);
        unsafe {
            SetPropW(hwnd, CONTROL_KIND, (kind as usize + 1) as _);
            SetWindowSubclass(hwnd, Some(style_control), 1, kind as usize);
        }
        if matches!(kind, Kind::Edit | Kind::Password | Kind::Number) {
            send(
                hwnd,
                EM_SETMARGINS,
                (EC_LEFTMARGIN | EC_RIGHTMARGIN) as usize,
                (scaled(10, self.scale) | (scaled(10, self.scale) << 16)) as isize,
            );
            crate::desktop::center_edit_text(hwnd, CONTROL_BG);
        }
        if matches!(kind, Kind::Combo) {
            send(
                hwnd,
                CB_SETITEMHEIGHT,
                usize::MAX,
                scaled(28, self.scale) as isize,
            );
            send(hwnd, CB_SETITEMHEIGHT, 0, scaled(28, self.scale) as isize);
            // Same rounded dark field as the wheel editor's selectors.
            crate::selector::style_selector(hwnd, true);
        }
        self.widgets.push(Widget {
            hwnd,
            field,
            page,
            bounds,
            kind,
        });
        Ok(())
    }

    pub(super) fn layout(&self) {
        let mut client = RECT::default();
        unsafe { GetClientRect(self.hwnd, &mut client) };
        let width = client.right.max(1);
        for widget in &self.widgets {
            let [x, y, w, h] = widget.bounds;
            // Horizontal coordinates stretch with the client; vertical rhythm follows DPI.
            let top = if y < 0 {
                client.bottom + scaled(y, self.scale)
            } else {
                scaled(y, self.scale)
            };
            let height = if h < 0 {
                client.bottom + scaled(h, self.scale)
            } else {
                scaled(h, self.scale)
            };
            unsafe {
                SetWindowPos(
                    widget.hwnd,
                    ptr::null_mut(),
                    x * width / CLIENT_WIDTH,
                    top,
                    w * width / CLIENT_WIDTH,
                    height.max(10),
                    SWP_NOZORDER | SWP_NOACTIVATE,
                );
            }
        }
        if !self.wheel_panel.is_null() {
            let x = 48 * width / CLIENT_WIDTH;
            let top = scaled(187, self.scale);
            unsafe {
                SetWindowPos(
                    self.wheel_panel,
                    ptr::null_mut(),
                    x,
                    top,
                    width - 2 * x,
                    (client.bottom - top - scaled(EDGE_PAD + FOOTER_HEIGHT + 8, self.scale)).max(1),
                    SWP_NOZORDER | SWP_NOACTIVATE,
                );
            }
        }
        unsafe { InvalidateRect(self.hwnd, ptr::null(), 1) };
    }

    pub(super) fn show_page(&self) {
        let provider = self.settings.active_tts_provider;
        for widget in &self.widgets {
            let correct_page = widget.page.is_none_or(|page| page == self.page);
            let visible = match widget.field {
                Some(Field::Wheels) => self.wheel_panel.is_null(),
                Some(Field::Download) => !self.cable_available,
                Some(Field::RefreshVoices) => provider == Provider::Cartesia,
                Some(
                    Field::VoiceId
                    | Field::VoiceName
                    | Field::VoiceHint
                    | Field::SaveVoice
                    | Field::DeleteVoice,
                ) => provider == Provider::Volcengine,
                // Speaker ID and its name labels sit beside Volcengine's two edits.
                None if widget.page == Some(Page::Tts) && widget.bounds[1] >= 437 => {
                    provider == Provider::Volcengine
                }
                _ => true,
            };
            let desired = correct_page && visible;
            if (unsafe { IsWindowVisible(widget.hwnd) } != 0) != desired {
                unsafe { ShowWindow(widget.hwnd, if desired { SW_SHOW } else { SW_HIDE }) };
            }
        }
        // Repaint the glass under controls that were hidden by the page switch.
        unsafe { InvalidateRect(self.hwnd, ptr::null(), 0) };
        if !self.wheel_panel.is_null() {
            unsafe {
                if self.page == Page::Wheels {
                    SetWindowPos(
                        self.wheel_panel,
                        HWND_TOP,
                        0,
                        0,
                        0,
                        0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
                    );
                    RedrawWindow(
                        self.wheel_panel,
                        ptr::null(),
                        ptr::null_mut(),
                        RDW_INVALIDATE | RDW_ALLCHILDREN,
                    );
                } else {
                    ShowWindow(self.wheel_panel, SW_HIDE);
                }
            };
        }
        unsafe {
            // Repaint the uncovered shell; shown controls invalidate themselves.
            RedrawWindow(self.hwnd, ptr::null(), ptr::null_mut(), RDW_INVALIDATE);
        }
    }
}
