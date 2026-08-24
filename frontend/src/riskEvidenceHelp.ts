type RiskEvidenceHelp = {
  label: string
  explanation: string
}

const RISK_EVIDENCE_HELP: Record<string, RiskEvidenceHelp> = {
  ai_generated_probability: {
    label: 'AI 生成概率',
    explanation: '数值越高，越像 AI 生成；达到阈值后请人工确认。',
  },
  aesthetic_score: {
    label: '美学评分',
    explanation: '分数越低，画面质量越值得检查。',
  },
  alpha_edge_ratio: {
    label: '半透明边缘比例',
    explanation: '数值越高，半透明边缘越多。',
  },
  area_below_resolution: {
    label: '分辨率面积不足',
    explanation: '图片面积不够当前分辨率要求，可能太小。',
  },
  artist_style_score: {
    label: '画风一致性评分',
    explanation: '分数越低，越不像当前数据集的画风。',
  },
  black_border_ratio: {
    label: '黑边比例',
    explanation: '数值越高，图片边缘越可能有黑边。',
  },
  black_pixel_ratio: {
    label: '黑色像素比例',
    explanation: '数值越高，图片越可能过暗。',
  },
  blockiness: {
    label: '块状伪影程度',
    explanation: '数值越高，压缩块越明显，建议检查。',
  },
  character_role_outlier: {
    label: '角色一致性异常',
    explanation: '与同角色图片差异较大，建议确认是否为同一角色。',
  },
  cross_artist_duplicate: {
    label: '跨画师重复',
    explanation: '可能与其他画师的图片重复，建议对照检查。',
  },
  decode_error: {
    label: '图片解码失败',
    explanation: '文件打不开，无法正常查看和检测。',
  },
  duplicate_exact: {
    label: '完全重复',
    explanation: '与同组图片完全相同，请选择保留哪一张。',
  },
  duplicate_semantic: {
    label: '语义近重复',
    explanation: '内容与同组图片很像，请对照确认。',
  },
  duplicate_visual: {
    label: '视觉重复',
    explanation: '外观与同组图片很像，请对照确认。',
  },
  edge_density: {
    label: '边缘密度',
    explanation: '数值越高，画面边缘和细节越多。',
  },
  extreme_aspect_ratio: {
    label: '长宽比极端',
    explanation: '图片过宽或过高，比例超出设置范围。',
  },
  fully_transparent_image: {
    label: '全透明图片',
    explanation: '图片完全透明，通常没有可见内容。',
  },
  fully_transparent_ratio: {
    label: '完全透明比例',
    explanation: '数值越高，图片透明区域越多。',
  },
  high_crop_loss: {
    label: '裁切损失较高',
    explanation: '按目标尺寸裁切会丢掉较多画面。',
  },
  high_frequency_ratio: {
    label: '高频细节比例',
    explanation: '数值过高可能有噪声或细碎伪影。',
  },
  in_domain_probability: {
    label: '目标域概率',
    explanation: '数值越低，越不像当前数据集要收集的内容。',
  },
  invalid_dimensions: {
    label: '图片尺寸无效',
    explanation: '图片宽或高无效，无法正常检测。',
  },
  laplacian_variance: {
    label: '清晰度参考值',
    explanation: '数值越低，图片可能越模糊；仅供参考。',
  },
  luminance_std: {
    label: '亮度变化度',
    explanation: '数值越低，画面明暗变化越少。',
  },
  mean_saturation: {
    label: '平均饱和度',
    explanation: '数值越高，整体颜色越鲜艳。',
  },
  metric_error: {
    label: '指标计算失败',
    explanation: '指标算不出来，请直接检查原图。',
  },
  ocr_text_area_ratio: {
    label: '文字覆盖率',
    explanation: '数值越高，图片中文字覆盖越多。',
  },
  ocr_text_box_count: {
    label: '文字区域数量',
    explanation: '数字越大，图片里的文字区域越多。',
  },
  ocr_text_region: {
    label: '文字区域',
    explanation: '这里显示检测到的文字位置，请核对是否应保留。',
  },
  no_decodable_frame: {
    label: '没有可解码帧',
    explanation: '动画或视频没有可读取的画面。',
  },
  pixel_limit_exceeded: {
    label: '像素数量超限',
    explanation: '图片太大，超过当前读取上限。',
  },
  rgb_entropy: {
    label: '色彩信息量',
    explanation: '数值越低，颜色变化越少，画面可能单调。',
  },
  source_changed: {
    label: '源文件已变化',
    explanation: '扫描时文件被改动，请重新扫描后再判断。',
  },
  transparent_pixel_ratio: {
    label: '透明像素比例',
    explanation: '数值越高，图片半透明或透明区域越多。',
  },
  upscale_required: {
    label: '需要放大',
    explanation: '图片需要放大，放大后可能不够清晰。',
  },
  watermark_probability: {
    label: '水印概率',
    explanation: '数值越高，越可能有水印；达到阈值请检查原图。',
  },
  white_border_ratio: {
    label: '白边比例',
    explanation: '数值越高，图片边缘越可能有白边。',
  },
  white_pixel_ratio: {
    label: '白色像素比例',
    explanation: '数值越高，图片越可能过亮或留白过多。',
  },
}

const FALLBACK_HELP: RiskEvidenceHelp = {
  label: '风险指标',
  explanation: '该指标由自动审计产生，暂无专门的中文说明，请结合检测值、阈值和原图人工判断。',
}

export function getRiskEvidenceLabel(code: string): string {
  return RISK_EVIDENCE_HELP[code]?.label ?? code
}

export function getRiskEvidenceExplanation(code: string): string {
  return RISK_EVIDENCE_HELP[code]?.explanation ?? FALLBACK_HELP.explanation
}
