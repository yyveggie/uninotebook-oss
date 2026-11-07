import { useEffect, useRef, useState } from 'react'
import { motion as Motion } from 'framer-motion'
import { useNavigate } from 'react-router-dom'
import { ScrollReveal } from './ui/ScrollReveal'
import useAuthStore from '../stores/authStore'
import useRuntimeStore from '../stores/runtimeStore'
import { ContactSection, FooterContact } from './LandingContact'
import './LandingPage.css'

const LANDING_TITLE_SUFFIX_VARIANTS = ['越深挖价值', '越激发灵感', '越明晰方向', '越记录全面']

const CAROUSEL_SLIDES = [
  {
    src: '/landing/feature-1.png',
    title: '永久记录的学习空间',
    description: '您的所有上传记录都会被保存，一键搜索快速查找记录。',
  },
  {
    src: ['/landing/feature-2.1.png', '/landing/feature-2.2.png'],
    splitVariant: 'horizontal-compact',
    splitKey: 's2',
    title: '文档解析与笔记管理',
    description: '用最先进的 AI 帮您解读文档，保留任何内容作为笔记，以免日后翻找。',
  },
  {
    src: '/landing/feature-3.png',
    title: '知识网络和灵感迸发',
    description: '把你所有的文件拆解成图，建立你的知识网络，同时用 AI 帮你自动生成各类灵感。',
  },
  {
    src: '/landing/feature-3.5.png',
    title: '我的创作空间',
    description: '在创作空间中与 AI 实时一起编辑文字，支持文本仿写、去AI味、翻译、润色与学术化改写等。',
  },
  {
    src: ['/landing/feature-4.1.png', '/landing/feature-4.2.png', '/landing/feature-4.3.png', '/landing/feature-4.4.png'],
    splitVariant: 'four-corners',
    title: '播客、测验与复习',
    description: '基于你上传的文档自动生成重点、测验、闪卡与播客，随时随地、边听边看的解读与复习。',
  },
  {
    src: ['/landing/feature-6.1.png', '/landing/feature-6.2.png'],
    splitVariant: 'diagonal-reverse',
    title: '自定义海报一目了然',
    description: '用自己的风格让文档变换为一张海报，重点细节一张图搞定，不再费时阅读全文。',
  },
  {
    src: ['/landing/feature-7.1.png', '/landing/feature-7.2.png'],
    splitVariant: 'diagonal',
    title: '一键生成 PPT',
    description: '上传文档后自动生成结构清晰的 PPT，省去排版烦恼，专注内容表达。',
  },
]

const CHANGELOG_ITEMS = [
  {
    version: '2026.01',
    title: '新增“我的创作空间”页面',
  },
  {
    version: '2025.11',
    title: 'UniNotebook 正式上线',
  },
]

function LandingImageSlot({ src, alt, className = '', isActive = false, splitVariant = 'diagonal', splitKey = '' }) {
  if (Array.isArray(src)) {
    return (
      <div className={`landing-image-slot dual-image-slot variant-${splitVariant} ${splitKey ? `split-${splitKey}` : ''} ${isActive ? 'active' : ''} ${className}`.trim()}>
        {src.map((item, index) => (
          <div key={`${item}-${index}`} className={`dual-image-panel dual-image-panel-${index + 1}`}>
            <img
              src={item}
              alt={`${alt} part ${index + 1}`}
              loading="lazy"
              className={`landing-image-slot-media dual-image-${index + 1}`}
            />
          </div>
        ))}
      </div>
    )
  }

  return (
    <div className={`landing-image-slot ${className}`.trim()}>
      <img
        src={src}
        alt={alt}
        loading="lazy"
        className="landing-image-slot-media"
        onError={(e) => {
          const target = e.currentTarget
          target.style.display = 'none'
          const fallback = target.nextElementSibling
          if (fallback) fallback.style.display = 'flex'
        }}
      />
      <div className="landing-image-slot-fallback">
        请将图片放到 public/landing 后刷新
      </div>
    </div>
  )
}

function KnowledgeNetworkCanvas() {
  const canvasRef = useRef(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    let width = canvas.clientWidth || window.innerWidth
    let height = canvas.clientHeight || window.innerHeight
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5)

    const prefersReducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches ?? false
    const isMobile = window.matchMedia?.('(max-width: 768px)')?.matches ?? false
    const saveDataEnabled = navigator.connection?.saveData === true
    const lowCpu = typeof navigator.hardwareConcurrency === 'number' && navigator.hardwareConcurrency <= 4

    const quality = prefersReducedMotion
      ? 'low'
      : (isMobile || saveDataEnabled || lowCpu ? 'medium' : 'high')

    const resize = () => {
      width = canvas.clientWidth || window.innerWidth
      height = canvas.clientHeight || window.innerHeight
      canvas.width = width * dpr
      canvas.height = height * dpr
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }

    resize()
    window.addEventListener('resize', resize)

    const NODE_COUNT = quality === 'high' ? 160 : quality === 'medium' ? 100 : 0
    const ROTATE_Y_SPEED = 0.00008
    const ROTATE_X_SPEED = 0.00003
    const RADIAL_JITTER_SPEED = 0.00055
    const DEPTH = 180
    const nodes = Array.from({ length: NODE_COUNT }, () => {
      // 节点在一个近似球状的三维云团中分布，而不是扁平圆盘
      const rTone = Math.random()
      let tone = 'color' // 默认绿色/蓝色节点
      if (rTone < 0.15) {
        tone = 'dark' // 深色节点
      } else if (rTone < 0.3) {
        tone = 'light' // 亮白节点
      }

      // 在单位球内均匀采样方向与半径
      const theta = Math.random() * Math.PI * 2
      const u = Math.random() * 2 - 1 // cos(phi) ∈ [-1, 1]
      const radius = Math.cbrt(Math.random())
      const sinPhi = Math.sqrt(1 - u * u)

      const dirX = sinPhi * Math.cos(theta)
      const dirY = sinPhi * Math.sin(theta)
      const dirZ = u

      // 略微横向拉长的球状云团
      const x3d = dirX * 260 * radius
      const y3d = dirY * 210 * radius
      const z3d = dirZ * 260 * radius

      const lifeSpan = 12000 + Math.random() * 10000 // 12-22 秒的生命周期（降低连接变化速度）

      return {
        x: x3d,
        y: y3d,
        // 将 z3d 平移一半 DEPTH，使 project 中减去 DEPTH * 0.5 后回到对称范围
        z: z3d + DEPTH * 0.5,
        speed: 0.005 + Math.random() * 0.007,
        tone,
        life: Math.random() * lifeSpan,
        lifeSpan,
      }
    })

    const links = []
    for (let i = 0; i < NODE_COUNT; i++) {
      for (let j = i + 1; j < NODE_COUNT; j++) {
        const linkProbability = quality === 'high' ? 0.028 : 0.02
        if (Math.random() < linkProbability) {
          links.push([i, j])
        }
      }
    }

    // 从四周流向中心的字符数据流（新增希腊字母）
    const CHAR_SET = '01 23 45 67 89 A B C D E F G H I J K L M N α β γ δ ε ζ η θ λ μ π ρ σ τ φ ω Δ Σ Ω 数据 学 习 知 识 连 接 星 球 网络'
    const DATA_STREAMS = [
      // 左上角 → 中心略偏左
      { from: { x: 0.04, y: 0.10 }, ctrl: { x: 0.20, y: 0.26 }, to: { x: 0.56, y: 0.46 } },
      // 右上角 → 中心略偏右
      { from: { x: 0.96, y: 0.12 }, ctrl: { x: 0.80, y: 0.30 }, to: { x: 0.62, y: 0.44 } },
      // 左下角 → 中心略偏左下
      { from: { x: 0.06, y: 0.92 }, ctrl: { x: 0.22, y: 0.72 }, to: { x: 0.56, y: 0.54 } },
      // 右下角 → 中心略偏右下
      { from: { x: 0.96, y: 0.88 }, ctrl: { x: 0.78, y: 0.68 }, to: { x: 0.62, y: 0.56 } },
      // 新增：顶部中间 → 中心
      { from: { x: 0.50, y: 0.02 }, ctrl: { x: 0.52, y: 0.25 }, to: { x: 0.58, y: 0.45 } },
      // 新增：底部中间 → 中心
      { from: { x: 0.50, y: 0.98 }, ctrl: { x: 0.48, y: 0.75 }, to: { x: 0.58, y: 0.55 } },
      // 新增：左侧中间 → 中心
      { from: { x: 0.02, y: 0.50 }, ctrl: { x: 0.25, y: 0.48 }, to: { x: 0.54, y: 0.50 } },
      // 新增：右侧中间 → 中心
      { from: { x: 0.98, y: 0.50 }, ctrl: { x: 0.75, y: 0.52 }, to: { x: 0.64, y: 0.50 } },
    ]

    const DATA_PARTICLE_COUNT = quality === 'high' ? 100 : quality === 'medium' ? 52 : 0
    const dataParticles = Array.from({ length: DATA_PARTICLE_COUNT }, () => {
      const streamIndex = Math.floor(Math.random() * DATA_STREAMS.length)
      const baseSpeed = 0.00005 + Math.random() * 0.00005
      const size = 10 + Math.random() * 6
      const charIndex = Math.floor(Math.random() * CHAR_SET.length)
      const alphaBase = 0.35 + Math.random() * 0.3
      return {
        streamIndex,
        t: Math.random(),
        speed: baseSpeed,
        char: CHAR_SET[charIndex],
        size,
        alphaBase,
      }
    })

    const project = (node, time) => {
      const perspective = 280

      // 让整团网络围绕 X/Y 轴缓慢旋转，增强“沉浸在网络中”的 3D 感
      const rotateY = time * ROTATE_Y_SPEED
      const rotateX = time * ROTATE_X_SPEED
      const cosY = Math.cos(rotateY)
      const sinY = Math.sin(rotateY)
      const cosX = Math.cos(rotateX)
      const sinX = Math.sin(rotateX)

      // 节点沿自身方向轻微“呼吸”伸缩，让连线不断重组，保持云团整体球状
      const radialJitter = 1 + 0.2 * Math.sin(time * RADIAL_JITTER_SPEED * node.speed + node.z * 0.02)

      // 以网络中心为原点旋转，z 轴略微前移，避免所有点都在很远处
      const ox = node.x * radialJitter
      const oy = node.y * radialJitter
      const oz = (node.z - DEPTH * 0.5) * radialJitter

      // 绕 Y 轴旋转（左右环绕）
      const x1 = ox * cosY - oz * sinY
      const z1 = ox * sinY + oz * cosY

      // 再绕 X 轴旋转（上下俯仰）
      const y2 = oy * cosX - z1 * sinX
      const z2 = oy * sinX + z1 * cosX

      const viewScale = 2.4
      const baseX = width * 0.6
      const baseY = height * 0.5
      const z = perspective + z2
      const scale = perspective / z
      const x = baseX + x1 * scale * viewScale
      const y = baseY + y2 * scale * viewScale * 1.15
      return { x, y, scale }
    }

    let animationFrameId = null
    let lastTime = performance.now()
    let isPaused = false

    const render = (now) => {
      if (isPaused) {
        animationFrameId = null
        return
      }

      const delta = now - lastTime
      lastTime = now

      ctx.clearRect(0, 0, width, height)

      // 更新每个节点的生命周期，超时后在新的位置“重生”
      for (const node of nodes) {
        node.life += delta
        if (node.life > node.lifeSpan) {
          // 重新采样一个新的三维位置和属性
          const rTone = Math.random()
          let tone = 'color'
          if (rTone < 0.15) {
            tone = 'dark'
          } else if (rTone < 0.3) {
            tone = 'light'
          }

          const theta = Math.random() * Math.PI * 2
          const u = Math.random() * 2 - 1
          const radius = Math.cbrt(Math.random())
          const sinPhi = Math.sqrt(1 - u * u)

          const dirX = sinPhi * Math.cos(theta)
          const dirY = sinPhi * Math.sin(theta)
          const dirZ = u

          const x3d = dirX * 260 * radius
          const y3d = dirY * 210 * radius
          const z3d = dirZ * 260 * radius

          const lifeSpan = 12000 + Math.random() * 10000

          node.x = x3d
          node.y = y3d
          node.z = z3d + DEPTH * 0.5
          node.tone = tone
          node.speed = 0.005 + Math.random() * 0.007
          node.life = 0
          node.lifeSpan = lifeSpan
        }
      }

      for (const [i, j] of links) {
        const a = project(nodes[i], now)
        const b = project(nodes[j], now)
        const minScale = Math.min(a.scale, b.scale)
        const alpha = Math.max(0, Math.min(1, (minScale - 0.35) / 0.9))
        if (alpha <= 0) continue
        ctx.lineWidth = 0.6 + minScale * 1.4
        ctx.strokeStyle = `rgba(99, 186, 154, ${alpha * 0.9})`
        ctx.beginPath()
        ctx.moveTo(a.x, a.y)
        ctx.lineTo(b.x, b.y)
        ctx.stroke()
      }

      // 绕四个角落向中心流动的发光字符流
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      for (const p of dataParticles) {
        const stream = DATA_STREAMS[p.streamIndex]
        // 使用本帧时间步推进 t（0-1），循环往复
        p.t += p.speed * delta
        if (p.t > 1) {
          p.t -= 1
          const idx = Math.floor(Math.random() * CHAR_SET.length)
          p.char = CHAR_SET[idx]
        }

        const tNorm = p.t
        // 平滑插值，靠近中间时速度稍微快一点
        const ease = tNorm * tNorm * (3 - 2 * tNorm)
        const oneMinus = 1 - ease

        const sx = stream.from.x * width
        const sy = stream.from.y * height
        const cx = stream.ctrl.x * width
        const cy = stream.ctrl.y * height
        const ex = stream.to.x * width
        const ey = stream.to.y * height

        // 二次贝塞尔曲线插值
        const x = oneMinus * oneMinus * sx + 2 * oneMinus * ease * cx + ease * ease * ex
        const y = oneMinus * oneMinus * sy + 2 * oneMinus * ease * cy + ease * ease * ey

        // 在路径两端逐渐淡入淡出
        const alpha = p.alphaBase * Math.sin(Math.PI * tNorm)
        if (alpha <= 0.02) continue

        ctx.save()
        ctx.globalAlpha = alpha
        ctx.fillStyle = 'rgba(148, 228, 213, 1)'
        ctx.shadowColor = 'rgba(45, 212, 191, 0.9)'
        ctx.shadowBlur = 14
        ctx.font = `${p.size}px ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", system-ui`
        ctx.fillText(p.char, x, y)
        ctx.restore()
      }

      for (const node of nodes) {
        const { x, y, scale } = project(node, now)
        const depthAlpha = Math.max(0, Math.min(1, (scale - 0.35) / 0.9))
        if (depthAlpha <= 0) continue

        // 节点生命周期的淡入/淡出，让“新节点生成”更有存在感
        const lifeProgress = node.life / node.lifeSpan
        const fadeEdge = 0.22
        let lifeAlpha = 1
        if (lifeProgress < fadeEdge) {
          lifeAlpha = lifeProgress / fadeEdge
        } else if (lifeProgress > 1 - fadeEdge) {
          lifeAlpha = (1 - lifeProgress) / fadeEdge
        }
        if (lifeAlpha <= 0.02) continue

        const twinkle = 0.6 + 0.4 * Math.sin(now / 520 + node.z * 0.03)
        const alpha = depthAlpha * twinkle * lifeAlpha

        const radius = 2.2 + 4.5 * scale
        const glowRadius = radius * 3

        const gradient = ctx.createRadialGradient(x, y, 0, x, y, glowRadius)
        if (node.tone === 'dark') {
          gradient.addColorStop(0, `rgba(15, 23, 42, ${alpha})`)
          gradient.addColorStop(0.4, `rgba(15, 23, 42, ${alpha * 0.7})`)
          gradient.addColorStop(1, 'rgba(15, 23, 42, 0)')
        } else if (node.tone === 'light') {
          gradient.addColorStop(0, `rgba(248, 250, 252, ${alpha})`)
          gradient.addColorStop(0.4, `rgba(226, 232, 240, ${alpha * 0.9})`)
          gradient.addColorStop(1, 'rgba(248, 250, 252, 0)')
        } else {
          gradient.addColorStop(0, `rgba(140, 184, 255, ${alpha})`)
          gradient.addColorStop(0.35, `rgba(90, 166, 198, ${alpha})`)
          gradient.addColorStop(1, 'rgba(255, 255, 255, 0)')
        }
        ctx.fillStyle = gradient
        ctx.beginPath()
        ctx.arc(x, y, glowRadius, 0, Math.PI * 2)
        ctx.fill()

        if (node.tone === 'dark') {
          ctx.fillStyle = `rgba(15, 23, 42, ${alpha * 1.1})`
        } else if (node.tone === 'light') {
          ctx.fillStyle = `rgba(248, 250, 252, ${alpha})`
        } else {
          ctx.fillStyle = `rgba(99, 186, 154, ${alpha * 1.4})`
        }
        ctx.beginPath()
        ctx.arc(x, y, radius, 0, Math.PI * 2)
        ctx.fill()
      }

      animationFrameId = requestAnimationFrame(render)
    }

    const handleVisibilityChange = () => {
      isPaused = document.hidden

      if (isPaused && animationFrameId) {
        cancelAnimationFrame(animationFrameId)
        animationFrameId = null
      }

      if (!isPaused && !animationFrameId && quality !== 'low') {
        lastTime = performance.now()
        animationFrameId = requestAnimationFrame(render)
      }
    }

    document.addEventListener('visibilitychange', handleVisibilityChange)

    if (quality !== 'low') {
      animationFrameId = requestAnimationFrame(render)
    }

    return () => {
      if (animationFrameId) {
        cancelAnimationFrame(animationFrameId)
      }
      document.removeEventListener('visibilitychange', handleVisibilityChange)
      window.removeEventListener('resize', resize)
    }
  }, [])

  return <canvas ref={canvasRef} className="knowledge-canvas" />
}

function LandingPage({ onStartLearning }) {
  const navigate = useNavigate()
  const user = useAuthStore(s => s.user)
  // 本地部署免登录、无服务提供方：隐藏登录入口与服务条款
  const isLocalMode = useRuntimeStore(s => s.deployMode === 'local')
  const setShowAuthModal = useAuthStore(s => s.setShowAuthModal)
  const titlePrefix = '你的学习资产，应该越用'
  const [typedSuffix, setTypedSuffix] = useState('')

  useEffect(() => {
    let phraseIndex = 0
    let charIndex = 0
    let isDeleting = false
    let timeoutId = null

    const tick = () => {
      const currentPhrase = LANDING_TITLE_SUFFIX_VARIANTS[phraseIndex]

      if (!isDeleting) {
        charIndex += 1
        setTypedSuffix(currentPhrase.slice(0, charIndex))

        if (charIndex >= currentPhrase.length) {
          isDeleting = true
          timeoutId = window.setTimeout(tick, 2600)
          return
        }

        timeoutId = window.setTimeout(tick, 160)
        return
      }

      charIndex -= 1
      setTypedSuffix(currentPhrase.slice(0, Math.max(0, charIndex)))

      if (charIndex <= 0) {
        isDeleting = false
        phraseIndex = (phraseIndex + 1) % LANDING_TITLE_SUFFIX_VARIANTS.length
        timeoutId = window.setTimeout(tick, 220)
        return
      }

      timeoutId = window.setTimeout(tick, 95)
    }

    timeoutId = window.setTimeout(tick, 240)

    return () => {
      if (timeoutId) {
        window.clearTimeout(timeoutId)
      }
    }
  }, [])

  const handlePrimaryAction = () => {
    // 本地版免登录：无论何时都直接进工作区，不存在登录弹窗
    if (user || isLocalMode) {
      onStartLearning?.()
    } else {
      setShowAuthModal(true)
    }
  }

  const [carouselIndex, setCarouselIndex] = useState(0)
  const carouselIndexRef = useRef(0)
  const viewportRef = useRef(null)
  const [trackOffset, setTrackOffset] = useState(0)
  const carouselTimerRef = useRef(null)

  const SLIDE_GAP = 24
  const SLIDE_PEEK = 48

  const computeLayout = () => {
    if (!viewportRef.current) return
    const vw = viewportRef.current.offsetWidth
    const sw = vw - SLIDE_PEEK * 2
    setTrackOffset(carouselIndexRef.current * (sw + SLIDE_GAP))
  }

  useEffect(() => {
    computeLayout()
    window.addEventListener('resize', computeLayout)
    return () => window.removeEventListener('resize', computeLayout)
  }, [])

  const startCarouselTimer = () => {
    if (carouselTimerRef.current) window.clearInterval(carouselTimerRef.current)
    carouselTimerRef.current = window.setInterval(() => {
      const next = (carouselIndexRef.current + 1) % CAROUSEL_SLIDES.length
      carouselIndexRef.current = next
      setCarouselIndex(next)
      if (viewportRef.current) {
        const vw = viewportRef.current.offsetWidth
        const sw = vw - SLIDE_PEEK * 2
        setTrackOffset(next * (sw + SLIDE_GAP))
      }
    }, 4000)
  }

  useEffect(() => {
    startCarouselTimer()
    return () => { if (carouselTimerRef.current) window.clearInterval(carouselTimerRef.current) }
  }, [])

  const goTo = (next) => {
    carouselIndexRef.current = next
    setCarouselIndex(next)
    if (viewportRef.current) {
      const vw = viewportRef.current.offsetWidth
      const sw = vw - SLIDE_PEEK * 2
      setTrackOffset(next * (sw + SLIDE_GAP))
    }
    startCarouselTimer()
  }

  const handleCarouselPrev = () => goTo((carouselIndexRef.current - 1 + CAROUSEL_SLIDES.length) % CAROUSEL_SLIDES.length)
  const handleCarouselNext = () => goTo((carouselIndexRef.current + 1) % CAROUSEL_SLIDES.length)

  return (
    <div className="landing-page knowledge-landing">
      <div className="knowledge-background">
        <KnowledgeNetworkCanvas />
      </div>

      <div className="knowledge-hero-stage">
        <Motion.div
          className="landing-content"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.8, ease: 'easeOut' }}
        >
          <ScrollReveal
            size="md"
            variant="dark"
            baseRotation={0}
            style={{ marginBottom: '0' }}
            textStyle={{
              lineHeight: 1.15,
              fontSize: 'clamp(24px, 3vw, 36px)',
              fontWeight: 800,
              color: '#020617'
            }}
            duration={0.8}
            staggerDelay={0.06}
          >
            创新源于连接并产生微小的改变
          </ScrollReveal>

          <ScrollReveal
            size="sm"
            variant="dark"
            splitMode="char"
            baseRotation={0}
            style={{ maxWidth: '860px', margin: '0', opacity: 0.9 }}
            textStyle={{
              lineHeight: '1.8',
              fontSize: '16px',
              fontWeight: 400,
              maxWidth: 'none',
              color: '#065f46',
              lineBreak: 'auto',
              wordBreak: 'normal'
            }}
            baseOpacity={0}
            duration={1}
            staggerDelay={0.02}
          >
            它不仅是一款笔记软件，更是你进行数据挖掘，知识管理，甚至是你灵感源泉的好帮手。
          </ScrollReveal>
          <div className="landing-actions">
            <button className="hero-btn primary" onClick={handlePrimaryAction}>
              {(user || isLocalMode) ? '进入我的学习空间' : '登录 / 注册'}
            </button>
          </div>
        </Motion.div>
      </div>

      <div className="landing-story" aria-label="产品功能展示">
        <section className="landing-band landing-band-text" id="features">
          <div className="landing-band-inner landing-text-layout">
            <h2 className="landing-text-title">
              {titlePrefix}
              <span className="landing-title-typed">{typedSuffix}</span>
              <span className="landing-title-cursor" aria-hidden="true">_</span>
            </h2>
            <div className="landing-text-right">
              <p className="landing-text-paragraph">
                创造力 = 过去的组合 + ε × (反馈质量 × 迭代次数)。<br />
                别只专注放大那一点点 ε（独特性），<br />
                真正需要的是反馈的质量和迭代的次数——<br />
                哪怕 ε 很小，也能迭代出惊人成果。<br />
              </p>
              <p className="landing-text-cite">— Sam Altman</p>
            </div>
          </div>
        </section>

        <section className="landing-band landing-band-changelog" id="changelog">
          <div className="landing-changelog-wrap">

            <div className="landing-changelog-list">
              {CHANGELOG_ITEMS.map((item) => (
                <article className="landing-changelog-item" key={item.version}>
                  <span className="landing-changelog-version">{item.version}</span>
                  <span className="landing-changelog-title">{item.title}</span>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="landing-band landing-band-showcase" id="showcase">
          <div className="landing-showcase-wrap">
            <h2 className="landing-showcase-heading">功能展示</h2>
            {/* 滑动视口：overflow hidden 裁切轨道 */}
            <div className="landing-showcase-viewport" ref={viewportRef}>
              <div
                className="landing-showcase-track"
                style={{ transform: `translateX(-${trackOffset}px)` }}
              >
                {CAROUSEL_SLIDES.map((slide, i) => (
                  <div
                    key={i}
                    className={`landing-showcase-slide ${i === carouselIndex ? 'active' : ''}`}
                  >
                    <div className="landing-showcase-img-frame">
                      <LandingImageSlot
                        src={slide.src}
                        alt={slide.title}
                        className={`landing-showcase-img ${slide.imageClass ?? ''}`}
                        isActive={i === carouselIndex}
                        splitVariant={slide.splitVariant}
                        splitKey={slide.splitKey}
                      />
                    </div>
                    <div className="landing-showcase-footer">
                      <p className="landing-showcase-title">{slide.title}</p>
                      <p className="landing-showcase-desc">{slide.description}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="landing-showcase-bottom">
              <div className="landing-showcase-dots">
                {CAROUSEL_SLIDES.map((_, i) => (
                  <button
                    key={i}
                    className={`landing-showcase-dot${i === carouselIndex ? ' landing-showcase-dot-active' : ''}`}
                    onClick={() => { goTo(i) }}
                    aria-label={`切换到第 ${i + 1} 张`}
                  />
                ))}
              </div>
              <div className="landing-showcase-controls">
                <button className="landing-showcase-nav" onClick={handleCarouselPrev} aria-label="上一张">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M15 18l-6-6 6-6"/></svg>
                </button>
                <button className="landing-showcase-nav" onClick={handleCarouselNext} aria-label="下一张">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M9 18l6-6-6-6"/></svg>
                </button>
              </div>
            </div>
          </div>
        </section>

        {/* 联系方式：本地部署无服务提供方，相应表单不适用 */}
        {!isLocalMode && <ContactSection />}

      {/* ========== Footer ========== */}
      <footer className="landing-footer">
        <div className="landing-footer-main">
          {/* 品牌列 */}
          <div className="landing-footer-brand-col">
            <span className="landing-footer-brand">UniNotebook</span>
            <p className="landing-footer-tagline">你的知识挖掘与管理助手</p>
            {/* 维护者邮箱不随源码发布，本地版不展示联系方式 */}
            {!isLocalMode && <FooterContact />}
          </div>

          {/* 产品列 */}
          <div className="landing-footer-col">
            <h4 className="landing-footer-col-title">产品</h4>
            <a className="landing-footer-link" href="#features" onClick={(e) => { e.preventDefault(); document.getElementById('features')?.scrollIntoView({ behavior: 'smooth' }); }}>功能介绍</a>
            <a className="landing-footer-link" href="#showcase" onClick={(e) => { e.preventDefault(); document.getElementById('showcase')?.scrollIntoView({ behavior: 'smooth' }); }}>产品展示</a>
            <a className="landing-footer-link" href="#changelog" onClick={(e) => { e.preventDefault(); document.getElementById('changelog')?.scrollIntoView({ behavior: 'smooth' }); }}>更新日志</a>
          </div>

          {/* 支持列 */}
          <div className="landing-footer-col">
            <h4 className="landing-footer-col-title">支持</h4>
            <a className="landing-footer-link" href="#" onClick={(e) => e.preventDefault()}>常见问题</a>
            <a className="landing-footer-link" href="#" onClick={(e) => e.preventDefault()}>使用文档</a>
          </div>

          {/* 条款列（本地部署无服务提供方，相应条款不适用） */}
          {!isLocalMode && (
            <div className="landing-footer-col">
              <h4 className="landing-footer-col-title">条款</h4>
              <a className="landing-footer-link" href="/legal/user-service-agreement" onClick={(e) => { e.preventDefault(); navigate('/legal/user-service-agreement') }}>用户服务协议</a>
              <a className="landing-footer-link" href="/legal/privacy-policy" onClick={(e) => { e.preventDefault(); navigate('/legal/privacy-policy') }}>隐私政策</a>
            </div>
          )}
        </div>

        <div className="landing-footer-bottom">
          <span className="landing-footer-copy">© {new Date().getFullYear()} UniNotebook. All rights reserved.</span>
          <span className="landing-footer-made">Made with ❤️ by AI Agents & overflowing</span>
        </div>
      </footer>

      </div>
    </div>
  )
}

export default LandingPage


