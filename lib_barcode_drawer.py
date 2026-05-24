# -*- coding: cp932 -*-
#必ずshift-jisにて保存を！！
import os
import datetime
import uuid
import logging
import traceback
import qrcode
from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont
import barcode
from barcode.writer import ImageWriter

# 呼び出し元のロガー設定を引き継ぐ
logger = logging.getLogger("BarcodeSystem.drawer")


def create_ean13(amount, idx, prefix, code, work_dir, invert_color=False):
    # ロギング用に、tryの外で空の変数を定義しておく（スコープ安全策）
    full_code = "generating..."
    try:
        """EAN13コード生成とチェックデジット計算"""
        # 負数は絶対値に変換してバーコード化（POSレジ制約対応）
        amt_val = abs(int(amount))
        # 100万円以上の場合は処理を拒否して例外を投げる
        if amt_val >= 1000000:
            raise ValueError(f"金額が7桁（1,000,000円以上）に達しているため、6桁固定のNon-PLU規格外です。値: {amt_val}")
        amount_str = f"{amt_val:06d}"
        # 接頭辞とコードを結合
        prefix_code = f"{prefix}{code}"
        base_tmp = f"{prefix_code}{amount_str}"
        # 末尾12桁を取得
        base = base_tmp[-12:]
        
        # チェックデジット計算(モジュラス10 ウェイト3)
        odd_sum = 0
        for j in range(0, 12, 2):
            odd_sum += int(base[j])
            
        even_sum = 0
        for j in range(1, 12, 2):
            even_sum += int(base[j])
            
        total_weighted = odd_sum + (even_sum * 3)
        remainder = total_weighted % 10
        cd = (10 - remainder) % 10
        
        full_code = f"{base}{cd}"
        
        # 画像設定（白黒反転の制御）
        writer_options = {
            'write_text': True,
            'module_height': 15.0,
            'background': 'white',
            'foreground': 'black'
        }
        
        if invert_color:
            # 返金時は背景を黒、バーを白にする
            writer_options['background'] = 'black'
            writer_options['foreground'] = 'white'
        
        
        
        # 一時ファイル名の生成（1行1処理）
        random_id = uuid.uuid4().hex[:6]
        fname_base = f"tmp_ean_{idx}_{random_id}"
        save_path_base = os.path.join(work_dir, fname_base)
        
        image_writer = ImageWriter()
        ean_obj = barcode.get('ean13', full_code, writer=ImageWriter())
        # 実ファイル保存
        res_path = ean_obj.save(save_path_base, options=writer_options)
        
        return res_path
    except Exception as e:
        logger.error(f"[CRITICAL] EAN13バーコード生成失敗(Code:{full_code}): {e}")
        logger.error(traceback.format_exc())
        raise # 呼び出し元に異常を伝え、描画を止める

def generate_pdf_logic(data, conf, work_dir, pdf_out_dir, print_func, is_preview=False):
    """描画メインロジック：座標計算・画像合成・出力実行"""
    paper_size = conf.get('paper_size', 'A6')
    
    # ==========================================================================
    # 【交通整理】80mmモードへ新設する専用の縦伸びロジックへ完全委譲
    # ==========================================================================
    if paper_size == '80mm':
        return _generate_80mm_logic(data, conf, work_dir, pdf_out_dir, print_func, is_preview)    
    
    
    # --- 以下、従来のA6/A4用処理---
    #設定ツールから印字モード（1D / 2D / BOTH）を安全にキャッチ
    layout_cfg = conf.get('layout', {})
    barcode_mode = conf.get('barcode_mode') or layout_cfg.get('BarcodeMode') or layout_cfg.get('barcodemode') or '1D'
    
    # 型安全：倍率の取得とキャスト
    raw_scale = conf.get('scale_rate', '1.0')
    scaler = float(raw_scale)
        
    # キャンバスサイズ確定（型安全キャスト）
    if paper_size == 'A4':
        canvas_base_w = 1240
        canvas_base_h = 1748
    elif paper_size == '80mm':
        # ※この古い分岐は上の交通整理でバイパスされるため実質不要
        canvas_base_w = 640
        canvas_base_h = 1600
    else:
        canvas_base_w = 874
        canvas_base_h = 1240
        
    final_w = int(canvas_base_w * scaler)
    final_h = int(canvas_base_h * scaler)
    
    # 画像生成
    canvas_img = Image.new('RGB', (final_w, final_h), 'white')
    canvas_draw = ImageDraw.Draw(canvas_img)
    
    # 座標変換係数（A6基点 874px）
    m_ratio = float(final_w) / 874.0


    def get_f_obj(size_val, is_bold=False):
        #代替日本語フォントのフォールバックリスト
        font_paths = ["msgothic.ttc", "meiryo.ttc", "msmincho.ttc", "yugothm.ttc"]
        
        base_s = int(size_val)
        calc_s = int(base_s * m_ratio)
        target_s = int(calc_s * 1.1) if is_bold else calc_s
        
        for f_path in font_paths:
            try:
                return ImageFont.truetype(f_path, target_s)
            except OSError:
                # 見つからないたびにWarningを吐くとログが秒速で肥大化するため黙殺（正常なフォールバック）ここの詳細は不要！！
                continue
                
        # 【フェイルセーフ】全て失敗した場合は、文字化け発行を防ぐため強制終了
        err_msg = f"必須の日本語フォントがシステムに見つかりません。探索先: {font_paths}"
        logger.critical(f"[FATAL] {err_msg}")
        raise RuntimeError(err_msg)
                
   
    # --- QRコード連結生成ロジック ---
    qr_data_list = []
    # 全階層から改行設定を総ざらい
    br_setting = conf.get('qr_line_break') or conf.get('QrLineBreak') or layout_cfg.get('QrLineBreak') or layout_cfg.get('qrlinebreak') or 'CR'
    br_map = {'CRLF': '\r\n', 'CR': '\r', 'LF': '\n', 'non': ''}
    br_code = br_map.get(br_setting, '\r')  # デフォルトは安全策として \r
    
    # INIやUIからテキストとして「\\r\\n」等の形式で入ってきた場合の完全翻訳ガード
    br_code = br_code.replace('\\r', '\r').replace('\\n', '\n')
    
    print_items_list = conf.get('print_items', [])
    data_amounts = data.get('amounts', {})
    
    # ディレイ設定の取得（請求用）
    is_delay_b = int(conf.get('enable_delay_billing', 0))
    delay_sym_b = conf.get('delay_symbol_billing', 'none')
    d_b = delay_sym_b if (is_delay_b == 1 and delay_sym_b != 'none') else ""
    
    for item_info in print_items_list:
        is_active = int(item_info.get('status', 1))
        if is_active == 1:
            item_nm = item_info['name']
            item_amt = int(data_amounts.get(item_nm, 0))
            if item_amt > 0:
                # 返金項目をメインQRのペイロードから完全に切り離す
                if "返金" not in item_nm:
                    #100万以上の場合はログを吐いてQR合成から排除
                    if item_amt >= 1000000:
                        logger.critical(f"[LIMIT ERROR] 項目 '{item_nm}' の金額({item_amt}円)が7桁のため、請求QRコードの生成対象からスキップされました。データが不正な可能性があります。")
                        continue
                    # EAN13文字列の生成（QR連結用）
                    amt_part = f"{item_amt:06d}"
                    prefix_code_part = f"{item_info['prefix']}{item_info['code']}"
                    full_raw = f"{prefix_code_part}{amt_part}"
                    # 12文字未満の異常データを検知し、ゼロ埋め補完した事実をログに刻印する
                    if len(full_raw) < 12:
                        logger.warning(f"[WARN] A6請求QR: データ長が12桁未満のため、先頭をゼロ埋め補完して生成を続行します。(項目: {item_info['name']}, 生データ: '{full_raw}')")
                    b_12 = full_raw.zfill(12)[-12:]
                    
                    # チェックデジット再計算
                    o_v = sum(int(b_12[j]) for j in range(0, 12, 2))
                    e_v = sum(int(b_12[j]) for j in range(1, 12, 2))
                    c_d_val = (10 - ((o_v + e_v * 3) % 10)) % 10
                    
                    qr_data_list.append(f"{b_12}{c_d_val}")
                
    # 請求総額が0円より大きい場合のみ、請求QRコードを生成・描画する
    if qr_data_list and barcode_mode in ['2D', 'BOTH']:
        #設定値と実際に使われる改行コードを可視化
        logger.debug("=== DEBUG ===")
        logger.debug(f"br_setting (UIから取得した値): {repr(br_setting)}")
        logger.debug(f"br_code (変換後の改行コード): {repr(br_code)}")
        # ============
        #請求用QRディレイ挿入：セパレータを [記号]\r[記号] に置換
        qr_sep = f"{d_b}{br_code}{d_b}" if d_b else br_code
        combined_qr_str = qr_sep.join(qr_data_list)
        
        #結合後の文字列に対してもテキストとしての改行記号の混入を完全防衛
        combined_qr_str = combined_qr_str.replace('\\r', '\r').replace('\\n', '\n')
        
        #QRに入れる最終文字列を可視化
        logger.debug(f"combined_qr_str (QRの中身): {repr(combined_qr_str)}")
        logger.debug("=============")
        qr_gen = qrcode.QRCode(version=1, box_size=10, border=1)
        qr_gen.add_data(combined_qr_str)
        qr_gen.make(fit=True)
        qr_image_obj = qr_gen.make_image(fill_color="black", back_color="white")
        
        # 型安全キャスト：QR座標とサイズ
        raw_qs = int(layout_cfg.get('QR_Size', 160))
        target_qs = int(raw_qs * m_ratio)
        
        raw_qx = int(layout_cfg.get('QR_X', 40))
        raw_qy = int(layout_cfg.get('QR_Y', 20))
        target_qx = int(raw_qx * m_ratio)
        target_qy = int(raw_qy * m_ratio)
        
        resized_qr = qr_image_obj.resize((target_qs, target_qs))
        canvas_img.paste(resized_qr, (target_qx, target_qy))

    # --- 返金専用QRコード追加描画（オープン価格マイナス入力） ---
    # ディレイ設定の取得（返金用）
    is_delay_r = int(conf.get('enable_delay_refund', 0))
    delay_sym_r = conf.get('delay_symbol_refund', 'none')
    
    # 【外科手術】キーワードを実体としてのスペースに変換
    if is_delay_r == 1:
        if delay_sym_r == "SPACE_1":
            d_r = " "
        elif delay_sym_r == "SPACE_5":
            d_r = " " * 5
        elif delay_sym_r == "SPACE_10":
            d_r = " " * 10
        elif delay_sym_r == "SPACE_50":
            d_r = " " * 50
        elif delay_sym_r == "SPACE_100":
            d_r = " " * 100
        elif delay_sym_r == "none" or delay_sym_r == "":
            d_r = ""
        else:
            d_r = delay_sym_r # その他の記号はそのまま使用
    else:
        d_r = ""

    for item_info in print_items_list:
        is_active = int(item_info.get('status', 1))
        if is_active == 1:
            item_nm = item_info['name']
            if "返金" in item_nm:
                item_amt = int(data_amounts.get(item_nm, 0))
                if item_amt > 0:
                    # 【7桁スキップ】
                    if item_amt >= 1000000:
                        logger.critical(f"[LIMIT ERROR] A6返金QR: 項目 '{item_info['name']}' の返金金額({item_amt}円)が7桁のため、返金QRの生成を完全に中止します。")
                        continue
                    # =======================================================
                    # ② 欄外用：返金専用QRコード　リーダーによって出来るかも！？念のためコメントアウトして保持。
                    # =======================================================
                    # # オープン価格用(000000固定)のベース文字列生成
                    # prefix_code_part = f"{item_info['prefix']}{item_info['code']}"
                    # base_12 = f"{prefix_code_part}000000"
                    
                    # # チェックデジット計算
                    # o_v = sum(int(base_12[j]) for j in range(0, 12, 2))
                    # e_v = sum(int(base_12[j]) for j in range(1, 12, 2))
                    # c_d_val = (10 - ((o_v + e_v * 3) % 10)) % 10
                    
                    # # 【外科手術】返金用QRディレイ挿入：EAN13+ [記号] + \r + [記号] + - + [記号] + 金額
                    # ean13_str = f"{base_12}{c_d_val}"
                    # refund_qr_payload = f"{ean13_str}{br_code}{d_r}-{item_amt}{br_code}"
                    
                  
                   # ②純粋な返金用Non-PLU
                    amt_part = f"{item_amt:06d}"
                    prefix_code_part = f"{item_info['prefix']}{item_info['code']}"
                    full_raw = f"{prefix_code_part}{amt_part}"
                    # 12文字未満の異常データを検知し、ゼロ埋め補完した事実をログに刻印する
                    if len(full_raw) < 12:
                        logger.warning(f"[WARN] A6返金QR: データ長が12桁未満のため、先頭をゼロ埋め補完して生成を続行します。(項目: {item_info['name']}, 生データ: '{full_raw}')")
                        
                    base_12 = full_raw.zfill(12)[-12:]
                    
                    # チェックデジット計算
                    o_v = sum(int(base_12[j]) for j in range(0, 12, 2))
                    e_v = sum(int(base_12[j]) for j in range(1, 12, 2))
                    c_d_val = (10 - ((o_v + e_v * 3) % 10)) % 10
                    
                    # RとNを完璧に減らす：末尾の「br_code」を完全撤去し、純粋な13桁のみに純化
                    ean13_str = f"{base_12}{c_d_val}"
                    refund_qr_payload = ean13_str  # ← 末尾の {br_code} を完全に削除
                    
                    # # === DEBUG (REFUND) ===
                    logger.debug("=== DEBUG (REFUND) ===")
                    logger.debug(f"br_setting (UI): {repr(br_setting)}")
                    logger.debug(f"refund_qr_payload (QR中身): {repr(refund_qr_payload)}")
                    logger.debug("========================")
  

     
                    # --- QR生成と貼り付け処理 ---
                    if barcode_mode in ['2D', 'BOTH']:
                        ref_qr_gen = qrcode.QRCode(version=1, box_size=10, border=1)
                        ref_qr_gen.add_data(refund_qr_payload)
                        ref_qr_gen.make(fit=True)
                        ref_qr_image = ref_qr_gen.make_image(fill_color="black", back_color="white")
                        
                        raw_rqs = int(layout_cfg.get('RefundQR_Size', 160))
                        target_rqs = int(raw_rqs * m_ratio)
                        
                        raw_rqx = int(layout_cfg.get('RefundQR_X', 650))
                        raw_rqy = int(layout_cfg.get('RefundQR_Y', 950))
                        target_rqx = int(raw_rqx * m_ratio)
                        target_rqy = int(raw_rqy * m_ratio)
                        
                        resized_rqr = ref_qr_image.resize((target_rqs, target_rqs))
                        canvas_img.paste(resized_rqr, (target_rqx, target_rqy))
                    
                    
                    
    # --- 共通ラベル・テキスト描画 ---
    # 新規/更新ラベル
    rev_fs_raw = int(layout_cfg.get('RevLabel_FontSize', 35))
    rev_font = get_f_obj(rev_fs_raw, True)
    rev_x = int(int(layout_cfg.get('RevLabel_X', 40)) * m_ratio)
    rev_y = int(int(layout_cfg.get('RevLabel_Y', 40)) * m_ratio)
    rev_txt = f"[{data.get('rev_label', '新規')}]"
    canvas_draw.text((rev_x, rev_y), rev_txt, font=rev_font, fill='black')
    
    # 印刷日時
    pd_fs_raw = int(layout_cfg.get('PrintDate_FontSize', 25))
    pd_font = get_f_obj(pd_fs_raw)
    pd_x = int(int(layout_cfg.get('PrintDate_X', 580)) * m_ratio)
    pd_y = int(int(layout_cfg.get('PrintDate_Y', 130)) * m_ratio)
    time_now = datetime.datetime.now().strftime('%Y/%m/%d %H:%M')
    canvas_draw.text((pd_x, pd_y), f"印刷日時：{time_now}", font=pd_font, fill='black')

    # タイトル（中央揃え計算含む）
    t_fs_raw = int(layout_cfg.get('Title_FontSize', 50))
    t_font = get_f_obj(t_fs_raw, True)
    title_str = conf.get('system_name', "領収明細票（薬局控）")
    
    t_x_conf = int(layout_cfg.get('Title_X', 0))
    if t_x_conf > 0:
        t_x_pos = int(t_x_conf * m_ratio)
    else:
        t_w_len = canvas_draw.textlength(title_str, font=t_font)
        t_x_pos = int((final_w - t_w_len) / 2)
        
    t_y_pos = int(int(layout_cfg.get('Title_Y', 30)) * m_ratio)
    canvas_draw.text((t_x_pos, t_y_pos), title_str, font=t_font, fill='black')
    
    # 患者氏名（右寄せおよび末尾切り捨てロジック）
    p_fs_raw = int(layout_cfg.get('Patient_FontSize', 50))
    p_font_obj = get_f_obj(p_fs_raw, True)
    p_x_limit = int(int(layout_cfg.get('Patient_X', 40)) * m_ratio)
    v_r_boundary = int(834 * m_ratio)
    p_y = int(int(layout_cfg.get('Patient_Y', 180)) * m_ratio)
    
    p_name = data.get('patient_name', '')
    p_full_txt = f"{p_name} 様"
    max_print_w = v_r_boundary - p_x_limit
    
    while canvas_draw.textlength(p_full_txt, font=p_font_obj) > max_print_w and len(p_name) > 0:
        p_name = p_name[:-1]
        p_full_txt = f"{p_name} 様"
        
    final_txt_w = canvas_draw.textlength(p_full_txt, font=p_font_obj)
    draw_x = v_r_boundary - final_txt_w
    canvas_draw.text((draw_x, p_y), p_full_txt, font=p_font_obj, fill='black')

    # --- 罫線（Line1-3） ---
    line_color = '#D3D3D3'
    for line_idx in range(1, 4):
        raw_ly = int(layout_cfg.get(f'Line{line_idx}_Y', 0))
        target_ly = int(raw_ly * m_ratio)
        if target_ly > 0:
            ls_x = int(int(layout_cfg.get(f'Line{line_idx}_StartX', 40)) * m_ratio)
            le_x = int(int(layout_cfg.get(f'Line{line_idx}_EndX', 834)) * m_ratio)
            line_w = int(2 * m_ratio)
            canvas_draw.line((ls_x, target_ly, le_x, target_ly), fill=line_color, width=line_w)

    # --- 項目別描画グリッド枠 ---
    grid_line_color = '#EEEEEE'
    v_l = int(40 * m_ratio)
    v_c = int(437 * m_ratio)
    v_r = int(834 * m_ratio)
    
    raw_line1_y_for_grid = int(layout_cfg.get('Line1_Y', 240))
    grid_top = int(raw_line1_y_for_grid * m_ratio)
    raw_line2_y_for_grid = int(layout_cfg.get('Line2_Y', 940))
    grid_bottom = int(raw_line2_y_for_grid * m_ratio)
    
    grid_w = int(2 * m_ratio)
    canvas_draw.line((v_l, grid_top, v_l, grid_bottom), fill=grid_line_color, width=grid_w)
    canvas_draw.line((v_c, grid_top, v_c, grid_bottom), fill=grid_line_color, width=grid_w)
    canvas_draw.line((v_r, grid_top, v_r, grid_bottom), fill=grid_line_color, width=grid_w)
    
    barcode_temp_list = []
    #一時ファイルが発生する可能性がある処理の直前からtryを開始する
    try:
        is_print_1d = int(conf.get('print_1d_barcode', 1))

        for idx, it_cfg in enumerate(print_items_list):
            it_active = int(it_cfg.get('status', 1))
            if it_active == 0:
                continue
                
            it_nm_val = it_cfg['name']
            try:
                it_amt_val = int(data_amounts.get(it_nm_val, 0))
            except (ValueError, TypeError) as e:
                # 単なるエラー文字列だけでなく、元のデータ型や行番号をスタックトレースで一網打尽にする
                logger.error(f"金額の数値変換失敗(項目:{it_nm_val}, 値:{data_amounts.get(it_nm_val)}): {e}", exc_info=True)
                it_amt_val = 0    
            
            it_x_raw = int(it_cfg.get('pos_x', 0))
            it_y_raw = int(it_cfg.get('pos_y', 0))
            it_x_final = int(it_x_raw * m_ratio)
            it_y_final = int(it_y_raw * m_ratio)
            
            it_n_fs_raw = int(layout_cfg.get('Item_Name_FontSize', 30))
            canvas_draw.text((it_x_final, it_y_final), f"【{it_nm_val}】", font=get_f_obj(it_n_fs_raw, True), fill='black')
            
            amt_str_formatted = f"{it_amt_val:,}"
            it_a_fs_raw = int(layout_cfg.get('Item_Amount_FontSize', 60))
            it_a_font = get_f_obj(it_a_fs_raw, True)
            it_a_xo = int(layout_cfg.get('Item_Amount_X_Offset', 410))
            it_a_yo = int(layout_cfg.get('Item_Amount_Y_Offset', 50))
            txt_w = canvas_draw.textlength(amt_str_formatted, font=it_a_font)
            amt_draw_x = int(it_x_final + (it_a_xo * m_ratio) - txt_w)
            amt_draw_y = int(it_y_final + (it_a_yo * m_ratio))
            canvas_draw.text((amt_draw_x, amt_draw_y), amt_str_formatted, font=it_a_font, fill='black')
            
            it_y_xo = int(layout_cfg.get('Item_Yen_X_Offset', 415))
            it_y_yo = int(layout_cfg.get('Item_Yen_Y_Offset', 70))
            it_y_fs_raw = int(layout_cfg.get('Item_Yen_FontSize', 30))
            yen_draw_x = int(it_x_final + (it_y_xo * m_ratio))
            yen_draw_y = int(it_y_final + (it_y_yo * m_ratio))
            canvas_draw.text((yen_draw_x, yen_draw_y), "円", font=get_f_obj(it_y_fs_raw), fill='black')
            
            if it_x_final < v_c:
                line_start = v_l
                line_end = v_c
            else:
                line_start = v_c
                line_end = v_r
                
            it_line_yo = int(layout_cfg.get('Item_Line_Y_Offset', 220))
            item_line_y = int(it_y_final + (it_line_yo * m_ratio))
            canvas_draw.line((line_start, item_line_y, line_end, item_line_y), fill=grid_line_color, width=grid_w)
            
            # ★設定が1D印字ON、かつ現在のモードが「1Dモード」の時のみ、商品ごとの1Dバーコードを描画する
            if is_print_1d == 1 and barcode_mode in ['1D', 'BOTH']:
                if it_amt_val > 0:
                    # ★項目名に「返金」が含まれるか判定し、invert_colorフラグとして渡す
                    #is_ref = ("返金" in it_nm_val)　#反転用。認識悪いので中止。tmp_pathに記載有。
                    try:
                        # 1Dバーコード画像の生成
                        tmp_path = create_ean13(it_amt_val, idx, it_cfg['prefix'], it_cfg['code'], work_dir)
                        #tmp_path = create_ean13(it_amt_val, idx, it_cfg['prefix'], it_cfg['code'], work_dir, invert_color=is_ref)
                        barcode_temp_list.append(tmp_path)
                        
                        # レイアウト設定からサイズを取得
                        bc_w_raw = int(layout_cfg.get('Item_Barcode_W', 280))
                        bc_h_raw = int(layout_cfg.get('Item_Barcode_H', 70))
                        
                        # 倍率を適用した最終サイズを計算
                        bc_final_w = int(bc_w_raw * m_ratio)
                        bc_final_h = int(bc_h_raw * m_ratio)
                        # 画像を開いてリサイズ
                        barcode_img = Image.open(tmp_path).resize((bc_final_w, bc_final_h))
                    except Exception as e:
                        logger.error(f"バーコード描画失敗(項目:{it_nm_val}, 金額:{it_amt_val}): {e}")   
                        continue   
                        
                    # 貼り付け座標の計算（エラーがなかった場合のみ実行される）    
                    bc_yo_raw = int(layout_cfg.get('Item_Barcode_Y_Offset', 140))
                    barcode_draw_y = int(it_y_final + (bc_yo_raw * m_ratio))
                    
                    # キャンバスへ合成
                    canvas_img.paste(barcode_img, (it_x_final, barcode_draw_y))

        # --- 受付情報BOX描画 ---
        box_w_conf = int(layout_cfg.get('Box_W', 0))
        if box_w_conf > 0:
            box_x = int(int(layout_cfg.get('Box_X', 40)) * m_ratio)
            box_y = int(int(layout_cfg.get('Box_Y', 940)) * m_ratio)
            box_h = int(int(layout_cfg.get('Box_H', 200)) * m_ratio)
            box_w = int(box_w_conf * m_ratio)
            box_line_w = int(4 * m_ratio)
            canvas_draw.rectangle([box_x, box_y, box_x + box_w, box_y + box_h], outline='black', width=box_line_w)
            
            # 日付と番号のサイズを個別に参照（プレビュー不一致解消用） ---
            box_l_fs = int(layout_cfg.get('BoxLabel_FontSize', 28))
            font_box_l = get_f_obj(box_l_fs)
            
            # 個別設定がない場合は BoxVal_FontSize(デフォルト70) を使用
            base_fs = int(layout_cfg.get('BoxVal_FontSize', 70))
            date_fs = int(layout_cfg.get('BoxDateVal_FontSize', base_fs))
            no_fs   = int(layout_cfg.get('BoxNoVal_FontSize', base_fs))
            
            font_date_v = get_f_obj(date_fs, True)
            font_no_v   = get_f_obj(no_fs, True)
            
            # 定義した個別のフォントを割り当てる ---
            # 1. 受付日の描画 (font_date_v を使用)
            bd_l_x = int(int(layout_cfg.get('BoxDateLabel_X', 55)) * m_ratio)
            bd_l_y = int(int(layout_cfg.get('BoxDateLabel_Y', 960)) * m_ratio)
            canvas_draw.text((bd_l_x, bd_l_y), "受付日", font=font_box_l, fill='black')
            
            bd_v_x = int(int(layout_cfg.get('BoxDateVal_X', 70)) * m_ratio)
            bd_v_y = int(int(layout_cfg.get('BoxDateVal_Y', 980)) * m_ratio)
            raw_disp_date = data.get('dispensing_date', '----/--/--')
            date_box_str = raw_disp_date[5:10] if len(raw_disp_date) >= 10 else "--/--"
            canvas_draw.text((bd_v_x, bd_v_y), date_box_str, font=font_date_v, fill='black')
            
            bn_l_x = int(int(layout_cfg.get('BoxNoLabel_X', 55)) * m_ratio)
            bn_l_y = int(int(layout_cfg.get('BoxNoLabel_Y', 1050)) * m_ratio)
            canvas_draw.text((bn_l_x, bn_l_y), "受付番号", font=font_box_l, fill='black')
            bn_v_x = int(int(layout_cfg.get('BoxNoVal_X', 100)) * m_ratio)
            bn_v_y = int(int(layout_cfg.get('BoxNoVal_Y', 1075)) * m_ratio)
            
            # 2. 受付番号の描画 (font_no_v を使用)
            receipt_no_val = data.get('receipt_no', '----')
            canvas_draw.text((bn_v_x, bn_v_y), str(receipt_no_val), font=font_no_v, fill='black')

        # --- 請求総額・負担割合 ---差額精算対応レイアウト ---
        ta_val_raw = data.get('total_amount', 0)
        total_text_disp = f"{int(ta_val_raw):,}円"
        ta_fs_raw = int(layout_cfg.get('TotalAmount_FontSize', 90))
        ta_font_obj = get_f_obj(ta_fs_raw, True)
        ta_y_pos_raw = int(layout_cfg.get('TotalAmount_Y', 1050))
        total_y_pos = int(ta_y_pos_raw * m_ratio)
        ta_x_pos_conf = int(layout_cfg.get('TotalAmount_X', 0))
        if ta_x_pos_conf > 0:
            total_x_pos = int(ta_x_pos_conf * m_ratio) - canvas_draw.textlength(total_text_disp, font=ta_font_obj)
        else:
            right_pad = int(50 * m_ratio)
            total_x_pos = (final_w - canvas_draw.textlength(total_text_disp, font=ta_font_obj) - right_pad)
        
        
        
        tl_x_pos = int(int(layout_cfg.get('TotalLabel_X', 450)) * m_ratio)
        tl_yo_val = int(layout_cfg.get('TotalLabel_Y_Offset', -50))
        total_label_y = total_y_pos + int(tl_yo_val * m_ratio)
        
        if data.get('is_adjustment'):
            # Parserから渡された外部注入ラベル定義（請求金額等）を取得
            rl = data.get('receipt_labels', {})
                    
            # 座標と行間の取得
            sub_x = int(int(layout_cfg.get('Adj_X', 40)) * m_ratio)
            sub_y_base = int(int(layout_cfg.get('Adj_Y_Base', 880)) * m_ratio)
            l_h = int(int(layout_cfg.get('Adj_L_H', 35)) * m_ratio)
                    
            #Editorで追加したフォントサイズ設定を反映
            main_fs = int(layout_cfg.get('Adj_Main_FS', 28)) # 主サイズ
            sub_fs = int(layout_cfg.get('Adj_Sub_FS', 22))   # 副サイズ（未収金等）
            main_f_obj = get_f_obj(main_fs)
            sub_f_obj = get_f_obj(sub_fs)
            
            # ---6項目の帳尻が視覚的に合うようにレイアウトを再構築 ---
            # 1. 請求金額 (Index 17)
            canvas_draw.text((sub_x, sub_y_base), f"{rl.get('presc', '今回請求額')}：{data.get('pure_presc', 0):,}円", font=main_f_obj, fill='black')
            
            # 2. 前回領収済 (Index 18) - 患者が前回いくら払ったかのエビデンス
            canvas_draw.text((sub_x, sub_y_base + l_h), f"{rl.get('paid', '前回領収額')}：{data.get('already_paid', 0):,}円", font=main_f_obj, fill='black')
            
            # --- Parser側で生成した動的ラベル（過払い/未収金）を適用し、絶対値で印字 ---
            prev_val = data.get('prev_unpaid', 0)
            curr_val = data.get('curr_unpaid', 0)
            
            # 3. 前回未収/過払い (Index 16)
            prev_label = rl.get('prev_unpaid_disp', '前回未収金')
            canvas_draw.text((sub_x, sub_y_base + l_h * 2), f"{prev_label}：{abs(prev_val):,}円", font=sub_f_obj, fill='black')
            
            # 4. 今回未収/過払い (Index 20)
            curr_label = rl.get('curr_unpaid_disp', '今回未収金')
            canvas_draw.text((sub_x, sub_y_base + l_h * 3), f"{curr_label}：{abs(curr_val):,}円", font=sub_f_obj, fill='black')
            
            #最終確定タイトル「精算領収額」の適用
            main_label = "精算領収額"
            
        else:
            # 通常時・再発行時は「領収合計額」
            main_label = "領収合計額"
            
        # ---精算超過（返金）時の白黒反転アラート表示 ---
        # 外部からの入力型（str/int）に依存しないよう明示的にintキャスト
        is_refund_total = (int(ta_val_raw) < 0)
        # ★プレビュー時、または実際にマイナス決済の時は座布団（黒背景）を敷く
        is_draw_refund_ui = is_refund_total or is_preview
        t_color = 'white' if is_refund_total else 'black'
        
        if is_refund_total:
            # 視覚的に「返金」を強調するため、ラベルと金額の背景を黒く塗りつぶす（座布団）
            pad = int(15 * m_ratio)
            rect_x1 = tl_x_pos - pad
            rect_y1 = total_label_y - pad
            rect_x2 = final_w - pad
            rect_y2 = total_y_pos + int(ta_fs_raw * 1.1 * m_ratio) # 金額の下枠まで
            canvas_draw.rectangle([rect_x1, rect_y1, rect_x2, rect_y2], fill='black')
            
        #タイトルと金額の描画（反転時は自動的に白抜き文字になる）
        canvas_draw.text((tl_x_pos, total_label_y), main_label, font=get_f_obj(40, True), fill=t_color)
        canvas_draw.text((int(total_x_pos), total_y_pos), total_text_disp, font=ta_font_obj, fill=t_color)
        
        # =======================================================
        # ★返金時の注意喚起テキスト(プレビュー時は強制描画）
        # =======================================================
        has_refund_qr = any(int(it.get('status', 1)) == 1 and "返金" in it['name'] and int(data_amounts.get(it['name'], 0)) > 0 for it in print_items_list)
        if has_refund_qr:
            line1 = conf.get('notice_text4', '')
            line2 = conf.get('notice_text5', '')
            
            caution_x = int(380 * m_ratio)
            caution_y = int(1070 * m_ratio)
            line_spacing = int(35 * m_ratio)
            caution_font = get_f_obj(24, is_bold=True)
            
            if line1.strip():
                canvas_draw.text((caution_x, caution_y), line1, font=caution_font, fill='black')
            if line2.strip():
                canvas_draw.text((caution_x, caution_y + line_spacing), line2, font=caution_font, fill='black')

        
        br_x = int(int(layout_cfg.get('BurdenRate_X', 40)) * m_ratio)
        br_y = int(int(layout_cfg.get('BurdenRate_Y', 1050)) * m_ratio)
        br_fs = int(layout_cfg.get('BurdenRate_FontSize', 40))
        p_rate = data.get('burden_rate', 0)
        br_txt = f"[負担割合] {p_rate}％"
        canvas_draw.text((br_x, br_y), br_txt, font=get_f_obj(br_fs, True), fill='black')
        
        # ---系統の注釈描画 ---
        # 注釈1 (全体)
        notice_text_val = conf.get('notice_text', '')
        if notice_text_val:
            n1_x = int(int(layout_cfg.get('Notice_X', 300)) * m_ratio)
            n1_y = int(int(layout_cfg.get('Notice_Y', 1170)) * m_ratio)
            n1_fs = int(layout_cfg.get('Notice_FontSize', 25))
            canvas_draw.text((n1_x, n1_y), str(notice_text_val), font=get_f_obj(n1_fs), fill='black')
            
        # 注釈2 (請求QR用)
        # 請求総額が0円より大きい（請求QRが存在する）場合のみ印字する
        notice_text2_val = conf.get('notice_text2', '')
        if notice_text2_val and len(qr_data_list) > 0 and barcode_mode in ['2D', 'BOTH']:
            n2_x = int(int(layout_cfg.get('Notice2_X', 40)) * m_ratio)
            n2_y = int(int(layout_cfg.get('Notice2_Y', 240)) * m_ratio)
            n2_fs = int(layout_cfg.get('Notice2_FontSize', 22))
            canvas_draw.text((n2_x, n2_y), str(notice_text2_val), font=get_f_obj(n2_fs), fill='black')
            
        # 注釈3 (返金QR用)
        # 実際に返金項目がアクティブかつ金額が1円以上（返金QRが存在する）場合のみ印字する
        has_refund_item = False
        for item_info in print_items_list:
            if int(item_info.get('status', 1)) == 1 and "返金" in item_info['name']:
                if int(data_amounts.get(item_info['name'], 0)) > 0:
                    has_refund_item = True
                    break

        notice_text3_val = conf.get('notice_text3', '')
        if notice_text3_val and has_refund_item and barcode_mode in ['2D', 'BOTH']:
            n3_x = int(int(layout_cfg.get('Notice3_X', 650)) * m_ratio)
            n3_y = int(int(layout_cfg.get('Notice3_Y', 1120)) * m_ratio)
            n3_fs = int(layout_cfg.get('Notice3_FontSize', 22))
            canvas_draw.text((n3_x, n3_y), str(notice_text3_val), font=get_f_obj(n3_fs), fill='black')
            
        # --- 管理情報（バージョン・ファイル名）の印字 ---
        version_name = data.get('version_info', 'v-------')
        raw_fname = data.get('filename', '')
        # 拡張子 .txt を除去
        base_fname = os.path.splitext(raw_fname)[0]
        admin_info_txt = f"{version_name} / {base_fname}"
        
        adm_x = int(40 * m_ratio)
        adm_y = int(1220 * m_ratio)
        adm_fs = 15
        canvas_draw.text((adm_x, adm_y), admin_info_txt, font=get_f_obj(adm_fs), fill='black')   
            

        # 出力段
        if is_preview:
            return canvas_img
        target_printer_nm = str(conf.get('printer', 'non')).lower()
        if target_printer_nm == "non":
            output_fname_val = str(data.get('filename', 'output'))
            pdf_save_path = os.path.join(pdf_out_dir, f"{output_fname_val}.pdf")
            canvas_img.save(pdf_save_path, "PDF")
        else:
            print_func(canvas_img, conf['printer'])
    except Exception as e:
        # どの患者の、どのファイルで描画が落ちたかを特定
        fname = data.get('filename', 'unknown')
        logger.critical(f"[FATAL] 描画ロジックで致命的エラーが発生しました(File: {fname})")
        logger.error(f"エラー詳細: {e}")
        logger.error(traceback.format_exc())
        raise

    finally:
        # 処理が終わったら、生成した一時バーコード画像を漏らさず綺麗にお掃除
        for t_file in barcode_temp_list:
            try:
                if os.path.exists(t_file):
                    os.remove(t_file)
            except Exception as e:
                # ??【未報告撲滅】PermissionErrorなのかFileNotFoundなのか、OSの生声を記録する
                logger.warning(f"[WARN] 一時ファイル削除失敗({t_file}): {e}", exc_info=True)

# ==============================================================================
# 【80mmロール紙専用】上から順に並べる数珠つなぎ（Running Y）描画ロジック
# ==============================================================================
def _generate_80mm_logic(data, conf, work_dir, pdf_out_dir, print_func, is_preview=False):
    """80mmロール紙専用ロジック（伝票刺し・指パラパラ仕分け極限最適化版）"""
    logger.info("80mmロール紙専用の縦伸び描画エンジンを起動しました。")
    
    # データの事前展開
    print_items_list = conf.get('print_items', [])
    data_amounts = data.get('amounts', {})
    # 【防弾】実機INIとUIモックの両方から確実に出力モードを取得
    barcode_mode = conf.get('barcode_mode') or conf.get('layout', {}).get('BarcodeMode') or conf.get('layout', {}).get('barcodemode') or '1D'
    
    canvas_w = 640
    max_canvas_h = 4000
    
    canvas_img = Image.new('RGB', (canvas_w, max_canvas_h), 'white')
    canvas_draw = ImageDraw.Draw(canvas_img)
    
    current_y = 30
    barcode_temp_list = []
    
    # 80mm専用フォントヘルパー
    def get_font(size_val, is_bold=False):
        font_paths = ["msgothic.ttc", "meiryo.ttc", "msmincho.ttc", "yugothm.ttc"]
        target_s = int(size_val * 1.1) if is_bold else int(size_val)
        for f_path in font_paths:
            try: 
                return ImageFont.truetype(f_path, target_s)
            except OSError:
                # 見つからないたびにWarningを吐くとログが秒速で肥大化するため黙殺（正常なフォールバック）ここの詳細は不要！！
                continue
        err_msg = f"[80mm FATAL] 必須の日本語フォントがWindowsシステムに見つかりません。探索先: {font_paths}"
        logger.critical(err_msg)
        raise RuntimeError(err_msg)

    try:
        # --- 一番最初に運用モード・プレビュー判定を確定させ、変数定義を最優先で確立する ---
        ta_val_raw = data.get('total_amount', 0)
        is_refund_total = (int(ta_val_raw) < 0)
        is_draw_refund_ui = is_refund_total or is_preview
        # ======================================================================
        # ① ヘッダー情報（管理情報・印刷日時・タイトル・患者名右寄せ）
        # ======================================================================
        admin_font = get_font(16)
        date_font = get_font(18)
        
        # 左端：追跡用の管理ヘッダー（最下部から引き揚げて一本化）
        version_name = data.get('version_info', 'v-------')
        raw_fname = data.get('filename', '')
        base_fname = os.path.splitext(raw_fname)[0]
        admin_info_txt = f"{version_name} / {base_fname}"
        canvas_draw.text((30, current_y), admin_info_txt, font=admin_font, fill='black')
        
        # 右端：印刷日時
        time_now = datetime.datetime.now().strftime('%Y/%m/%d %H:%M')
        date_txt = f"印刷日時：{time_now}"
        date_w = canvas_draw.textlength(date_txt, font=date_font)
        canvas_draw.text((640 - 30 - date_w, current_y), date_txt, font=date_font, fill='black')
        
        current_y += 50

        title_font = get_font(36, is_bold=True)
        title_str = conf.get('system_name', "領収明細票（薬局控）")
        title_w = canvas_draw.textlength(title_str, font=title_font)
        canvas_draw.text((int((640 - title_w) / 2), current_y), title_str, font=title_font, fill='black')
        
        current_y += 65

        patient_font = get_font(42, is_bold=True)
        p_name = data.get('patient_name', '')
        p_full_txt = f"{p_name} 様"
        max_p_width = 640 - 60
        
        while canvas_draw.textlength(p_full_txt, font=patient_font) > max_p_width and len(p_name) > 0:
            p_name = p_name[:-1]
            p_full_txt = f"{p_name} 様"
            
        p_w = canvas_draw.textlength(p_full_txt, font=patient_font)
        canvas_draw.text((640 - 30 - p_w, current_y), p_full_txt, font=patient_font, fill='black')
        current_y += 65

        layout_cfg = conf.get('layout', {}) # 防弾用
        # --- 1-4. 請求QRコード（氏名のすぐ下） ---
        # 初期化をif文の外に脱出させ、1Dモード時のNameError即死を完全防衛
        qr_data_list = []
        
        if barcode_mode in ['2D', 'BOTH']:

            # 全階層から改行設定を総ざらい
            br_setting = conf.get('qr_line_break') or conf.get('QrLineBreak') or layout_cfg.get('QrLineBreak') or layout_cfg.get('qrlinebreak') or 'CR'
            br_map = {'CRLF': '\r\n', 'CR': '\r', 'LF': '\n', 'non': ''}
            br_code = br_map.get(br_setting, '\r').replace('\\r', '\r').replace('\\n', '\n')
            
            is_delay_b = int(conf.get('enable_delay_billing', 0))
            delay_sym_b = conf.get('delay_symbol_billing', 'none')
            d_b = delay_sym_b if (is_delay_b == 1 and delay_sym_b != 'none') else ""

            for item_info in print_items_list:
                if int(item_info.get('status', 1)) == 1:
                    item_nm = item_info['name']
                    item_amt = int(data_amounts.get(item_nm, 0))
                    if item_amt > 0 and "返金" not in item_nm:
                        #【7桁スキップ】
                        if item_amt >= 1000000:
                            logger.critical(f"[LIMIT ERROR] 80mm請求QR: 項目 '{item_nm}' の金額({item_amt}円)が7桁のため、生成からスキップされました。")
                            continue
                        amt_part = f"{item_amt:06d}"
                        prefix_code_part = f"{item_info['prefix']}{item_info['code']}"
                        full_raw = f"{prefix_code_part}{amt_part}"
                        # 12文字未満の異常データを検知し、ゼロ埋め補完した事実をログに刻印する
                        if len(full_raw) < 12:
                            logger.warning(f"[WARN] 80mm請求QR: データ長が12桁未満のため、先頭をゼロ埋め補完して生成を続行します。(項目: {item_info['name']}, 生データ: '{full_raw}')")
                        
                        b_12 = full_raw.zfill(12)[-12:]
                        
                        o_v = sum(int(b_12[j]) for j in range(0, 12, 2))
                        e_v = sum(int(b_12[j]) for j in range(1, 12, 2))
                        c_d_val = (10 - ((o_v + e_v * 3) % 10)) % 10
                        qr_data_list.append(f"{b_12}{c_d_val}")

            if qr_data_list:
                qr_sep = f"{d_b}{br_code}{d_b}" if d_b else br_code
                combined_qr_str = qr_sep.join(qr_data_list).replace('\\r', '\r').replace('\\n', '\n')
                
                qr_gen = qrcode.QRCode(version=1, box_size=10, border=1)
                qr_gen.add_data(combined_qr_str)
                qr_gen.make(fit=True)
                qr_img_obj = qr_gen.make_image(fill_color="black", back_color="white")
                
                qr_size = 240
                resized_qr = qr_img_obj.resize((qr_size, qr_size))
                qr_x = int((640 - qr_size) / 2)
                canvas_img.paste(resized_qr, (qr_x, current_y))
                current_y += qr_size + 6
                
                caption_font = get_font(18)
                cap_w = canvas_draw.textlength("（請求QRコード）", font=caption_font)
                canvas_draw.text((int((640 - cap_w) / 2), current_y), "（請求QRコード）", font=caption_font, fill='black')
                current_y += 25

        # ヘッダーを締めくくる区切り線
        canvas_draw.line((30, current_y, 640 - 30, current_y), fill='#D3D3D3', width=3)
        current_y += 40

        # ======================================================================
        # ② 金額アイテムの明細行 ＆ 商品別1Dバーコード
        # ======================================================================
        is_print_1d = int(conf.get('print_1d_barcode', 1))
        hide_zero_items = 0
        
        # GUIの設定値（0円非表示）を取得
        # （セクション名の大文字・小文字ブレを吸収して安全に取得）
        common_cfg = conf.get('Common') or conf.get('common') or {}
        try:
            # 1. まずUIプレビュー用の conf から取得を試みる
            common_cfg = conf.get('Common') or conf.get('common') or {}
            if 'HideZeroAmountItems' in common_cfg:
                hide_zero_items = int(common_cfg.get('HideZeroAmountItems', 0))
            else:
                # 2. 本番環境（親が渡し忘れた場合）は、同じ階層のINIを直接見に行く
                import configparser
                ini_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'barcode_setting.ini')
                if os.path.exists(ini_path):
                    temp_c = configparser.ConfigParser()
                    temp_c.read(ini_path, encoding='cp932')
                    hide_zero_items = temp_c.getint('Common', 'HideZeroAmountItems', fallback=0)
        except Exception as e:
            logger.warning(f"0円非表示設定の取得に失敗しました: {e}")
            hide_zero_items = 0
        
        item_name_font = get_font(28, is_bold=True)
        item_amt_font = get_font(36, is_bold=True)
        item_yen_font = get_font(26)
        grid_line_color = '#EEEEEE'
        
        for idx, it_cfg in enumerate(print_items_list):
            it_active = int(it_cfg.get('status', 1))
            if it_active == 0:
                continue
                
            it_nm_val = it_cfg['name']
            try:
                it_amt_val = int(data_amounts.get(it_nm_val, 0))
            except (ValueError, TypeError) as e:
                # 単なるエラー文字列だけでなく、元のデータ型や行番号をスタックトレースで一網打尽にする
                logger.error(f"金額の数値変換失敗(項目:{it_nm_val}, 値:{data_amounts.get(it_nm_val)}): {e}", exc_info=True)
                it_amt_val = 0
            
            # GUIで非表示設定がON(1) かつ 金額が0円の場合は、描画せずに次へスキップ（間を詰める）
            if hide_zero_items == 1 and it_amt_val == 0:
                continue
                
            canvas_draw.text((30, current_y), f"【{it_nm_val}】", font=item_name_font, fill='black')
            
            amt_str = f"{it_amt_val:,}"
            yen_str = "円"
            yen_w = canvas_draw.textlength(yen_str, font=item_yen_font)
            amt_w = canvas_draw.textlength(amt_str, font=item_amt_font)
            
            yen_x = 640 - 30 - yen_w
            amt_x = yen_x - amt_w - 10
            
            canvas_draw.text((amt_x, current_y - 4), amt_str, font=item_amt_font, fill='black')
            canvas_draw.text((yen_x, current_y + 4), yen_str, font=item_yen_font, fill='black')
            
            current_y += 55
            
            if is_print_1d == 1 and barcode_mode in ['1D', 'BOTH'] and it_amt_val > 0:
                try:
                    tmp_path = create_ean13(it_amt_val, idx, it_cfg['prefix'], it_cfg['code'], work_dir)
                    barcode_temp_list.append(tmp_path)
                    
                    bc_w = 360
                    bc_h = 80
                    barcode_img = Image.open(tmp_path).resize((bc_w, bc_h))
                    bc_x = int((640 - bc_w) / 2)
                    canvas_img.paste(barcode_img, (bc_x, current_y))
                    current_y += bc_h + 15
                except Exception as e:
                    logger.error(f"80mm明細バーコード描画失敗(項目:{it_nm_val}): {e}")
            
            current_y += 5
            canvas_draw.line((30, current_y, 640 - 30, current_y), fill=grid_line_color, width=2)
            current_y += 25

        # ======================================================================
        # ③ 精算情報（左右2列・完全右揃え「円」配置） ＆ 返金QR ＆ 合計金額
        # ======================================================================
        if data.get('is_adjustment'):
            adj_font = get_font(24)
            rl = data.get('receipt_labels', {})
            
            # --- 1段目左右配置（左の壁:X=30/右端:X=310 | 右の壁:X=340/右端:X=610） ---
            # 左列：今回請求額
            lbl_presc = f"{rl.get('presc', '今回請求額')}："
            canvas_draw.text((30, current_y), lbl_presc, font=adj_font, fill='black')
            amt_presc = f"{data.get('pure_presc', 0):,}円"
            w_presc = canvas_draw.textlength(amt_presc, font=adj_font)
            canvas_draw.text((310 - w_presc, current_y), amt_presc, font=adj_font, fill='black') # 右端から逆算
            
            # 右列：前回領収額
            lbl_paid = f"{rl.get('paid', '前回領収額')}："
            canvas_draw.text((340, current_y), lbl_paid, font=adj_font, fill='black')
            amt_paid = f"{data.get('already_paid', 0):,}円"
            w_paid = canvas_draw.textlength(amt_paid, font=adj_font)
            canvas_draw.text((610 - w_paid, current_y), amt_paid, font=adj_font, fill='black') # 右端から逆算
            
            current_y += 35
            
            # --- 2段目左右配置 ---
            # 左列：前回未収金/過払い
            prev_val = data.get('prev_unpaid', 0)
            prev_label = f"{rl.get('prev_unpaid_disp', '前回未収金')}："
            canvas_draw.text((30, current_y), prev_label, font=adj_font, fill='black')
            amt_prev = f"{abs(prev_val):,}円"
            w_prev = canvas_draw.textlength(amt_prev, font=adj_font)
            canvas_draw.text((310 - w_prev, current_y), amt_prev, font=adj_font, fill='black')
            
            # 右列：今回未収金/過払い
            curr_val = data.get('curr_unpaid', 0)
            curr_label = f"{rl.get('curr_unpaid_disp', '今回未収金')}："
            canvas_draw.text((340, current_y), curr_label, font=adj_font, fill='black')
            amt_curr = f"{abs(curr_val):,}円"
            w_curr = canvas_draw.textlength(amt_curr, font=adj_font)
            canvas_draw.text((610 - w_curr, current_y), amt_curr, font=adj_font, fill='black')
            
            current_y += 50
            
            main_label = "精算領収額"
            # Parserから版情報(U0, U1等)を取得して動的に結合する
            rev_raw = data.get('rev_label', 'U0')
            ux_prefix = rev_raw[:2] if len(rev_raw) >= 2 else "U0"
            type_label = f"[{ux_prefix}差額精算]"
        else:
            main_label = "領収合計額"
            type_label = f"[{data.get('rev_label', '新規')}]"

        # --- 3-2. 返金専用QRコードの配置 ---
        if barcode_mode in ['2D', 'BOTH']:
            refund_qr_payload = None
            for item_info in print_items_list:
                if int(item_info.get('status', 1)) == 1 and "返金" in item_info['name']:
                    item_amt = int(data_amounts.get(item_info['name'], 0))
                    if item_amt > 0:
                        # 【7桁スキップ】
                        if item_amt >= 1000000:
                            logger.critical(f"[LIMIT ERROR] 80mm返金QR: 項目 '{item_info['name']}' の返金金額({item_amt}円)が7桁のため、返金QRの生成を完全に中止します。")
                            continue
                        amt_part = f"{item_amt:06d}"
                        prefix_code_part = f"{item_info['prefix']}{item_info['code']}"
                        full_raw = f"{prefix_code_part}{amt_part}"
                        
                        # 12文字未満の異常データを検知し、ゼロ埋め補完した事実をログに刻印する
                        if len(full_raw) < 12:
                            logger.warning(f"[WARN] 80mm返金QR: データ長が12桁未満のため、先頭をゼロ埋め補完して生成を続行します。(項目: {item_info['name']}, 生データ: '{full_raw}')")
                        
                        base_12 = full_raw.zfill(12)[-12:]
                        
                        o_v = sum(int(base_12[j]) for j in range(0, 12, 2))
                        e_v = sum(int(base_12[j]) for j in range(1, 12, 2))
                        c_d_val = (10 - ((o_v + e_v * 3) % 10)) % 10
                        refund_qr_payload = f"{base_12}{c_d_val}"

            if refund_qr_payload:
                ref_qr_gen = qrcode.QRCode(version=1, box_size=10, border=1)
                ref_qr_gen.add_data(refund_qr_payload)
                ref_qr_gen.make(fit=True)
                ref_qr_img = ref_qr_gen.make_image(fill_color="black", back_color="white")
                
                # --- 返金QRの直前・直後に注釈4と5をサンドイッチ(中央揃え) ---
                caution_font = get_font(22, is_bold=True)
                
                
                
                # 1. 注釈4（※【返品】ボタン）
                if is_draw_refund_ui:
                    line1 = conf.get('notice_text4', '')
                    if line1.strip():
                        l1_w = canvas_draw.textlength(line1, font=caution_font)
                        canvas_draw.text((int((640 - l1_w) / 2), current_y), line1, font=caution_font, fill='black')
                        current_y += 35 # 下へ進める
                
                qr_size = 240
                resized_ref_qr = ref_qr_img.resize((qr_size, qr_size))
                ref_qr_x = int((640 - qr_size) / 2)
                canvas_img.paste(resized_ref_qr, (ref_qr_x, current_y))
                current_y += qr_size + 6
                
                # 3. 【下段】注釈5（ 先に押すこと!!）
                if is_draw_refund_ui:
                    line2 = conf.get('notice_text5', '')
                    if line2.strip():
                        l2_w = canvas_draw.textlength(line2, font=caution_font)
                        canvas_draw.text((int((640 - l2_w) / 2), current_y), line2, font=caution_font, fill='black')
                        current_y += 35 # 下へ進める
                
                caption_font = get_font(18)
                cap_w = canvas_draw.textlength("（返金QRコード）", font=caption_font)
                canvas_draw.text((int((640 - cap_w) / 2), current_y), "（返金QRコード）", font=caption_font, fill='black')
                current_y += 35

        # 合計金額座布団
        ta_val_raw = data.get('total_amount', 0)
        total_text_disp = f"{int(ta_val_raw):,}円"
        ta_font = get_font(46, is_bold=True)
        tl_font = get_font(32, is_bold=True)
        
        is_refund_total = (int(ta_val_raw) < 0)
        # ★プレビュー表示の時、または実際の返金時は警告表示モードへ
        is_draw_refund_ui = is_refund_total or is_preview
        t_color = 'white' if is_refund_total else 'black'
        
        if is_refund_total:
            canvas_draw.rectangle([30, current_y - 8, 640 - 30, current_y + 68], fill='black')
            
        ta_w = canvas_draw.textlength(total_text_disp, font=ta_font)
        canvas_draw.text((45, current_y + 8), main_label, font=tl_font, fill=t_color)
        canvas_draw.text((640 - 45 - ta_w, current_y), total_text_disp, font=ta_font, fill=t_color)
        current_y += 95
        

        # --- 3-4. 3系統の注釈テキスト描画 ---
        notice_font = get_font(22)
        for n_key in ['notice_text', 'notice_text2', 'notice_text3']:
            n_txt = conf.get(n_key, '')
            if n_txt.strip():
                canvas_draw.text((30, current_y), str(n_txt), font=notice_font, fill='black')
                current_y += 32

        # ======================================================================
        # ④ フッター（【伝票刺し・指パラパラめくり完全最適化】領域）
        # ======================================================================
        current_y += 20
        footer_label_font = get_font(26, is_bold=True)
        
        # --- 伝票種別 と 負担割合 を完全1行マージ配置！ ---
        # めくった瞬間に視線移動ゼロで、種別と％を同時に看破できる神レイアウト
        canvas_draw.text((30, current_y), f"【伝票種別】 {type_label}", font=footer_label_font, fill='black')
        
        p_rate = data.get('burden_rate', 0)
        rate_txt = f"【負担割合】 {p_rate}％"
        rate_w = canvas_draw.textlength(rate_txt, font=footer_label_font)
        # 固定座標340をやめ、右端(640-30=610)から文字幅を逆算して配置
        canvas_draw.text((640 - 30 - rate_w, current_y), rate_txt, font=footer_label_font, fill='black')
        current_y += 55
        
        # --- 【最下部固定・超巨大化】受付日 ＆ 受付番号 ---
        big_info_font = get_font(36, is_bold=True)
        
        # 受付日 (左端)
        raw_disp_date = data.get('dispensing_date', '----/--/--')
        date_box_str = raw_disp_date[5:10] if len(raw_disp_date) >= 10 else "--/--"
        canvas_draw.text((30, current_y), f"受付日：{date_box_str}", font=big_info_font, fill='black')
        
        # 受付番号 (右端へ自動密着)
        receipt_no_val = data.get('receipt_no', '----')
        no_txt = f"受付番号：{receipt_no_val}"
        no_w = canvas_draw.textlength(no_txt, font=big_info_font)
        canvas_draw.text((640 - 30 - no_w, current_y), no_txt, font=big_info_font, fill='black')
        
        current_y += 65
        
        # 4. 描き終わった実際の縦サイズ（current_y）の場所でスパッと切り落とす！
        final_img = canvas_img.crop((0, 0, canvas_w, current_y))
        
        # 5. 出力制御
        if is_preview:
            return final_img
            
        target_printer_nm = str(conf.get('printer', 'non')).lower()
        if target_printer_nm == "non":
            output_fname_val = str(data.get('filename', 'output'))
            pdf_save_path = os.path.join(pdf_out_dir, f"{output_fname_val}.pdf")
            final_img.save(pdf_save_path, "PDF")
            logger.info(f"[80mm] PDFファイルとして保存しました: {pdf_save_path}")
        else:
            print_func(final_img, conf['printer'])
            logger.info(f"[80mm] プリンタ '{conf['printer']}' へ直接画像を送信しました。")
            
    except Exception as e:
        fname = data.get('filename', 'unknown')
        logger.critical(f"[FATAL] 80mm描画ロジックで致命的エラーが発生しました(File: {fname}): {e}")
        logger.error(traceback.format_exc())
        raise
    finally:
        # 処理が終わったら、生成した一時バーコード画像を漏らさず綺麗にお掃除
        for t_file in barcode_temp_list:
            try:
                if os.path.exists(t_file):
                    os.remove(t_file)
            except Exception as e:
                # PermissionErrorなのかFileNotFoundなのか、OSの生声を記録する
                logger.warning(f"[WARN] 一時ファイル削除失敗({t_file}): {e}", exc_info=True)
                
# Copyright (c) 2026 ph-SIM133
# All rights reserved.
# This software is for non-commercial use only.