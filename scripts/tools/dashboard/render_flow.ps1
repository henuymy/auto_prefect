# Render the dashboard execution flow diagram.
param(
    [string]$OutputPath = "runtime/diagrams/dashboard-flow.png"
)

Add-Type -AssemblyName System.Drawing

$width = 3200
$height = 2200
$bitmap = [System.Drawing.Bitmap]::new($width, $height)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
$graphics.Clear([System.Drawing.Color]::White)

$fontFamily = [System.Drawing.FontFamily]::new("Noto Sans SC")
$fontTitle = [System.Drawing.Font]::new($fontFamily, 60, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
$fontSubtitle = [System.Drawing.Font]::new($fontFamily, 30, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
$fontStage = [System.Drawing.Font]::new($fontFamily, 36, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
$fontNode = [System.Drawing.Font]::new($fontFamily, 32, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
$fontBody = [System.Drawing.Font]::new($fontFamily, 30, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
$fontSmall = [System.Drawing.Font]::new($fontFamily, 26, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
$fontCode = [System.Drawing.Font]::new($fontFamily, 28, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)

$ink = [System.Drawing.ColorTranslator]::FromHtml("#152238")
$muted = [System.Drawing.ColorTranslator]::FromHtml("#64748B")
$line = [System.Drawing.ColorTranslator]::FromHtml("#94A3B8")
$panel = [System.Drawing.ColorTranslator]::FromHtml("#F8FAFC")
$blue = [System.Drawing.ColorTranslator]::FromHtml("#2563EB")
$blueFill = [System.Drawing.ColorTranslator]::FromHtml("#EAF2FF")
$green = [System.Drawing.ColorTranslator]::FromHtml("#15803D")
$greenFill = [System.Drawing.ColorTranslator]::FromHtml("#EAF8EF")
$red = [System.Drawing.ColorTranslator]::FromHtml("#C62828")
$redFill = [System.Drawing.ColorTranslator]::FromHtml("#FFF0F0")
$purple = [System.Drawing.ColorTranslator]::FromHtml("#7C3AED")
$purpleFill = [System.Drawing.ColorTranslator]::FromHtml("#F5F0FF")
$grayFill = [System.Drawing.ColorTranslator]::FromHtml("#F1F5F9")
$amber = [System.Drawing.ColorTranslator]::FromHtml("#B45309")
$amberFill = [System.Drawing.ColorTranslator]::FromHtml("#FFF7E6")

function New-RoundedPath {
    param([float]$X, [float]$Y, [float]$W, [float]$H, [float]$Radius)
    $path = [System.Drawing.Drawing2D.GraphicsPath]::new()
    $d = $Radius * 2
    $path.AddArc($X, $Y, $d, $d, 180, 90)
    $path.AddArc($X + $W - $d, $Y, $d, $d, 270, 90)
    $path.AddArc($X + $W - $d, $Y + $H - $d, $d, $d, 0, 90)
    $path.AddArc($X, $Y + $H - $d, $d, $d, 90, 90)
    $path.CloseFigure()
    return $path
}

function Draw-RoundedRect {
    param(
        [float]$X, [float]$Y, [float]$W, [float]$H,
        [System.Drawing.Color]$Fill, [System.Drawing.Color]$Stroke,
        [float]$StrokeWidth = 3, [float]$Radius = 24
    )
    $path = New-RoundedPath $X $Y $W $H $Radius
    $brush = [System.Drawing.SolidBrush]::new($Fill)
    $pen = [System.Drawing.Pen]::new($Stroke, $StrokeWidth)
    $graphics.FillPath($brush, $path)
    $graphics.DrawPath($pen, $path)
    $brush.Dispose()
    $pen.Dispose()
    $path.Dispose()
}

function Draw-Text {
    param(
        [string]$Text, [System.Drawing.Font]$Font,
        [System.Drawing.Color]$Color,
        [float]$X, [float]$Y, [float]$W, [float]$H,
        [string]$Align = "Center"
    )
    $format = [System.Drawing.StringFormat]::new()
    $format.LineAlignment = [System.Drawing.StringAlignment]::Center
    $format.Trimming = [System.Drawing.StringTrimming]::EllipsisWord
    if ($Align -eq "Left") {
        $format.Alignment = [System.Drawing.StringAlignment]::Near
    } elseif ($Align -eq "Right") {
        $format.Alignment = [System.Drawing.StringAlignment]::Far
    } else {
        $format.Alignment = [System.Drawing.StringAlignment]::Center
    }
    $brush = [System.Drawing.SolidBrush]::new($Color)
    $rect = [System.Drawing.RectangleF]::new($X, $Y, $W, $H)
    $graphics.DrawString($Text, $Font, $brush, $rect, $format)
    $brush.Dispose()
    $format.Dispose()
}

function Draw-Node {
    param(
        [string]$Title, [string]$Detail,
        [float]$X, [float]$Y, [float]$W, [float]$H,
        [System.Drawing.Color]$Fill, [System.Drawing.Color]$Stroke
    )
    Draw-RoundedRect $X $Y $W $H $Fill $Stroke 4 24
    if ($Detail) {
        Draw-Text $Title $fontNode $ink ($X + 18) ($Y + 12) ($W - 36) 52
        Draw-Text $Detail $fontSmall $muted ($X + 24) ($Y + 65) ($W - 48) ($H - 75)
    } else {
        Draw-Text $Title $fontNode $ink ($X + 20) ($Y + 10) ($W - 40) ($H - 20)
    }
}

function Draw-Arrow {
    param(
        [float]$X1, [float]$Y1, [float]$X2, [float]$Y2,
        [System.Drawing.Color]$Color = $blue,
        [float]$StrokeWidth = 5,
        [switch]$Dashed
    )
    $pen = [System.Drawing.Pen]::new($Color, $StrokeWidth)
    $pen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $pen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    if ($Dashed) {
        $pen.DashStyle = [System.Drawing.Drawing2D.DashStyle]::Dash
    }
    $graphics.DrawLine($pen, $X1, $Y1, $X2, $Y2)
    $angle = [Math]::Atan2($Y2 - $Y1, $X2 - $X1)
    $size = 18
    $p1 = [System.Drawing.PointF]::new(
        $X2 - $size * [Math]::Cos($angle - 0.55),
        $Y2 - $size * [Math]::Sin($angle - 0.55)
    )
    $p2 = [System.Drawing.PointF]::new(
        $X2 - $size * [Math]::Cos($angle + 0.55),
        $Y2 - $size * [Math]::Sin($angle + 0.55)
    )
    $brush = [System.Drawing.SolidBrush]::new($Color)
    $graphics.FillPolygon($brush, @([System.Drawing.PointF]::new($X2, $Y2), $p1, $p2))
    $brush.Dispose()
    $pen.Dispose()
}

function Draw-ElbowArrow {
    param(
        [float[]]$Points,
        [System.Drawing.Color]$Color,
        [float]$StrokeWidth = 5,
        [switch]$Dashed
    )
    $pen = [System.Drawing.Pen]::new($Color, $StrokeWidth)
    $pen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $pen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    $pen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
    if ($Dashed) {
        $pen.DashStyle = [System.Drawing.Drawing2D.DashStyle]::Dash
    }
    [System.Drawing.PointF[]]$pointList = @()
    for ($i = 0; $i -lt $Points.Length; $i += 2) {
        $pointList += [System.Drawing.PointF]::new($Points[$i], $Points[$i + 1])
    }
    $graphics.DrawLines($pen, $pointList)
    $last = $pointList[-1]
    $previous = $pointList[-2]
    $angle = [Math]::Atan2($last.Y - $previous.Y, $last.X - $previous.X)
    $size = 18
    $p1 = [System.Drawing.PointF]::new(
        $last.X - $size * [Math]::Cos($angle - 0.55),
        $last.Y - $size * [Math]::Sin($angle - 0.55)
    )
    $p2 = [System.Drawing.PointF]::new(
        $last.X - $size * [Math]::Cos($angle + 0.55),
        $last.Y - $size * [Math]::Sin($angle + 0.55)
    )
    $brush = [System.Drawing.SolidBrush]::new($Color)
    $graphics.FillPolygon($brush, @($last, $p1, $p2))
    $brush.Dispose()
    $pen.Dispose()
}

function Draw-Diamond {
    param(
        [string]$Text,
        [float]$CenterX, [float]$CenterY,
        [float]$W, [float]$H,
        [System.Drawing.Color]$Fill,
        [System.Drawing.Color]$Stroke
    )
    [System.Drawing.PointF[]]$points = @(
        [System.Drawing.PointF]::new($CenterX, $CenterY - $H / 2),
        [System.Drawing.PointF]::new($CenterX + $W / 2, $CenterY),
        [System.Drawing.PointF]::new($CenterX, $CenterY + $H / 2),
        [System.Drawing.PointF]::new($CenterX - $W / 2, $CenterY)
    )
    $brush = [System.Drawing.SolidBrush]::new($Fill)
    $pen = [System.Drawing.Pen]::new($Stroke, 4)
    $graphics.FillPolygon($brush, $points)
    $graphics.DrawPolygon($pen, $points)
    Draw-Text $Text $fontNode $ink ($CenterX - $W * 0.32) ($CenterY - $H * 0.28) ($W * 0.64) ($H * 0.56)
    $brush.Dispose()
    $pen.Dispose()
}

function Draw-StageHeader {
    param([string]$Number, [string]$Title, [float]$X, [float]$W)
    Draw-RoundedRect $X 235 $W 82 $grayFill $line 2 20
    Draw-RoundedRect ($X + 16) 252 48 48 $blue $blue 1 24
    Draw-Text $Number $fontSmall ([System.Drawing.Color]::White) ($X + 16) 250 48 48
    Draw-Text $Title $fontStage $ink ($X + 80) 240 ($W - 96) 68 "Left"
}

# Header
Draw-Text "数据驾驶舱执行逻辑与数据流" $fontTitle $ink 100 55 3000 90
Draw-Text "独立 MySQL · 复用登录与地市平台请求 · Prefect 每 5 分钟调度" $fontSubtitle $muted 100 145 3000 48
$headerPen = [System.Drawing.Pen]::new($blue, 6)
$graphics.DrawLine($headerPen, 100, 210, 3100, 210)
$headerPen.Dispose()

# Stage backgrounds and headings
$stageXs = @(100, 700, 1300, 1900, 2500)
foreach ($stageX in $stageXs) {
    Draw-RoundedRect $stageX 335 500 1450 $panel ([System.Drawing.ColorTranslator]::FromHtml("#E2E8F0")) 2 28
}
Draw-StageHeader "1" "触发与会话" 100 500
Draw-StageHeader "2" "平台采集" 700 500
Draw-StageHeader "3" "校验与决策" 1300 500
Draw-StageHeader "4" "MySQL 事务" 1900 500
Draw-StageHeader "5" "查询与展示" 2500 500

# Stage 1
Draw-Node "Prefect 触发" "每 5 分钟调度 / API 手动触发" 145 390 410 150 $blueFill $blue
Draw-Node "创建采集批次" "batch_no · collection_run = RUNNING" 145 590 410 150 $blueFill $blue
Draw-Node "获取互斥锁" "Prefect 并发限制 + MySQL advisory lock" 145 790 410 150 $blueFill $blue
Draw-Node "Session 探活" "仅要求 city_ops stage" 145 990 410 150 $blueFill $blue
Draw-Diamond "会话有效？" 350 1325 390 210 $amberFill $amber
Draw-Node "锁内复检并自动重登" "复用 session_manager，不重写登录流程" 145 1515 410 165 $amberFill $amber

Draw-Arrow 350 540 350 590
Draw-Arrow 350 740 350 790
Draw-Arrow 350 940 350 990
Draw-Arrow 350 1140 350 1220
Draw-Text "否" $fontSmall $red 520 1310 60 40
Draw-ElbowArrow @(545,1325,620,1325,620,1598,555,1598) $red 5
Draw-ElbowArrow @(350,1515,350,1450,620,1450,620,1185,350,1185) $amber 4

# Stage 2
Draw-Node "加载独立指标配置" "config/dashboard/，不读取通报任务配置" 745 390 410 150 $blueFill $blue
Draw-Node "分公司 BRANCH" "请求根区域及分公司数据" 745 620 410 150 $blueFill $blue
Draw-Node "网格 GRID" "按 areaCode 继续下钻" 745 850 410 150 $blueFill $blue
Draw-Node "渠道 CHANNEL" "采集首期最细层级" 745 1080 410 150 $blueFill $blue
Draw-RoundedRect 745 1340 410 145 $grayFill $line 3 22
Draw-Text "STAFF 不采集" $fontNode $muted 765 1350 370 52
Draw-Text "首期明确排除人员层级" $fontSmall $muted 765 1400 370 55

Draw-Text "是" $fontSmall $green 555 1225 60 40
Draw-ElbowArrow @(545,1270,650,1270,650,465,745,465) $green 5
Draw-Arrow 950 540 950 620
Draw-Arrow 950 770 950 850
Draw-Arrow 950 1000 950 1080
Draw-ElbowArrow @(950,1230,950,1290,1190,1290,1190,1412,1155,1412) $line 3 -Dashed

# Stage 3
Draw-Diamond "所有请求成功？" 1550 520 390 220 $blueFill $blue
Draw-Node "宽表转长表" "区域 × 指标；统一数值类型" 1345 710 410 155 $blueFill $blue
Draw-Node "完整性校验" "区域、指标、数值、层级关系" 1345 930 410 155 $blueFill $blue
Draw-Diamond "数据全部有效？" 1550 1255 390 220 $blueFill $blue
Draw-Node "整批失败" "不更新 current / snapshot" 1345 1510 410 160 $redFill $red

Draw-Arrow 1550 630 1550 710
Draw-Arrow 1550 865 1550 930
Draw-Arrow 1550 1085 1550 1145
Draw-Text "否" $fontSmall $red 1740 500 60 40
Draw-ElbowArrow @(1745,520,1810,520,1810,1590,1755,1590) $red 5
Draw-Text "否" $fontSmall $red 1740 1235 60 40
Draw-ElbowArrow @(1745,1255,1810,1255,1810,1590,1755,1590) $red 5

# Stage 4 transaction boundary
Draw-RoundedRect 1930 370 440 1280 $purpleFill $purple 7 30
Draw-Text "单一 MySQL 事务" $fontStage $purple 1955 390 390 65
Draw-Text "全部成功后一次提交" $fontSmall $muted 1955 445 390 45

$tableY = @(525, 705, 885, 1065, 1245)
$tableTitles = @(
    "dashboard_area",
    "dashboard_indicator",
    "dashboard_metric_`nsnapshot",
    "dashboard_metric_`ncurrent",
    "dashboard_`ncollection_run"
)
$tableDetails = @(
    "UPSERT 区域树",
    "同步独立指标定义",
    "插入 30 天历史快照",
    "UPSERT 最新成功值",
    "更新状态为 SUCCESS"
)
for ($i = 0; $i -lt $tableY.Count; $i++) {
    Draw-RoundedRect 1970 $tableY[$i] 360 135 ([System.Drawing.Color]::White) $purple 3 20
    Draw-Text $tableTitles[$i] $fontCode $purple 1985 ($tableY[$i] + 5) 330 64
    Draw-Text $tableDetails[$i] $fontSmall $muted 1985 ($tableY[$i] + 70) 330 50
    if ($i -lt $tableY.Count - 1) {
        Draw-Arrow 2150 ($tableY[$i] + 135) 2150 $tableY[$i + 1] $purple 4
    }
}
Draw-Node "提交事务" "快照与最新值同时可见" 1970 1450 360 135 $greenFill $green
Draw-Arrow 2150 1380 2150 1450 $green 5

Draw-Text "是" $fontSmall $green 1740 1155 60 40
Draw-ElbowArrow @(1745,1200,1850,1200,1850,440,1930,440) $green 5
Draw-ElbowArrow @(1550,1510,1550,1730,2150,1730,2150,1650) $red 5
Draw-Text "collection_run = FAILED" $fontSmall $red 1570 1680 500 46

# Stage 5
Draw-Node "FastAPI 查询接口" "状态 · 汇总 · 排名 · 趋势" 2545 420 410 160 $greenFill $green
Draw-Node "最新值与排名" "读取`ndashboard_metric_`ncurrent" 2545 700 410 155 $purpleFill $purple
Draw-Node "趋势与分钟变化" "读取`ndashboard_metric_`nsnapshot" 2545 950 410 155 $purpleFill $purple
Draw-Node "React 数据驾驶舱" "指标卡 · 区域筛选 · 排名 · 趋势图" 2545 1280 410 180 $greenFill $green
Draw-RoundedRect 2545 1535 410 120 $green $green 2 24
Draw-Text "展示最近一次成功数据" $fontNode ([System.Drawing.Color]::White) 2565 1545 370 100

Draw-ElbowArrow @(2330,1518,2420,1518,2420,500,2545,500) $green 5
Draw-ElbowArrow @(2330,1132,2470,1132,2470,777,2545,777) $purple 4 -Dashed
Draw-ElbowArrow @(2330,952,2435,952,2435,1027,2545,1027) $purple 4 -Dashed
Draw-Arrow 2750 580 2750 700 $green 5
Draw-ElbowArrow @(2750,855,2750,1170,2680,1170,2680,1280) $green 5
Draw-ElbowArrow @(2750,1105,2820,1105,2820,1280) $green 5
Draw-Arrow 2750 1460 2750 1535 $green 5

# Legend and exclusion note
Draw-RoundedRect 100 1840 1950 250 $grayFill $line 2 26
Draw-Text "明确隔离" $fontStage $ink 135 1860 230 55 "Left"
Draw-Text "自动通报原数据库、Excel 转换、数据比对和企业微信链路均不参与驾驶舱采集。" $fontBody $ink 135 1920 1840 70 "Left"
Draw-Text "驾驶舱仅复用：登录 · Session · 地市平台 HTTP 请求 · Prefect 调度" $fontSmall $muted 135 1995 1840 55 "Left"

Draw-RoundedRect 2110 1840 990 250 ([System.Drawing.Color]::White) $line 2 26
Draw-Text "颜色说明" $fontStage $ink 2145 1860 200 55 "Left"
$legendItems = @(
    @{ X = 2150; Y = 1940; Color = $blue; Text = "正常流程" },
    @{ X = 2380; Y = 1940; Color = $green; Text = "成功提交" },
    @{ X = 2610; Y = 1940; Color = $red; Text = "失败回滚" },
    @{ X = 2840; Y = 1940; Color = $purple; Text = "独立 MySQL" }
)
foreach ($item in $legendItems) {
    Draw-RoundedRect $item.X $item.Y 42 42 $item.Color $item.Color 1 10
    Draw-Text $item.Text $fontSmall $ink ($item.X + 55) ($item.Y - 5) 170 52 "Left"
}
Draw-Text "灰色虚线表示逻辑关联，不代表主处理顺序。" $fontSmall $muted 2145 2010 900 50 "Left"

$resolvedOutput = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $OutputPath))
$outputDirectory = [System.IO.Path]::GetDirectoryName($resolvedOutput)
[System.IO.Directory]::CreateDirectory($outputDirectory) | Out-Null
$bitmap.Save($resolvedOutput, [System.Drawing.Imaging.ImageFormat]::Png)

$graphics.Dispose()
$bitmap.Dispose()
$fontTitle.Dispose()
$fontSubtitle.Dispose()
$fontStage.Dispose()
$fontNode.Dispose()
$fontBody.Dispose()
$fontSmall.Dispose()
$fontCode.Dispose()
$fontFamily.Dispose()

Write-Output $resolvedOutput
