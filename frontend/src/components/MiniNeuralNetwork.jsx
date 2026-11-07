import { useEffect, useRef } from 'react'
import { motion as Motion } from 'framer-motion'

function MiniNeuralNetwork({ width = 120, height = 120 }) {
  const canvasRef = useRef(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    let animationFrameId
    const dpr = window.devicePixelRatio || 1

    // Resize function
    const resize = () => {
      canvas.width = width * dpr
      canvas.height = height * dpr
      canvas.style.width = `${width}px`
      canvas.style.height = `${height}px`
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }
    resize()

    // Config for Small Scale
    const NODE_COUNT = 80 // 增加节点数量以适应更大尺寸
    const DEPTH = 120

    // 缩放因子：增加填充比例
    const scaleFactor = Math.min(width, height) * 0.42

    const nodes = Array.from({ length: NODE_COUNT }, () => {
      const rTone = Math.random()
      let tone = 'color' // 默认颜色
      if (rTone < 0.2) tone = 'dark' // 深色
      if (rTone > 0.8) tone = 'light' // 亮色

      // 随机球状分布
      const theta = Math.random() * Math.PI * 2
      const u = Math.random() * 2 - 1
      const radius = Math.cbrt(Math.random())
      const sinPhi = Math.sqrt(1 - u * u)

      const dirX = sinPhi * Math.cos(theta)
      const dirY = sinPhi * Math.sin(theta)
      const dirZ = u

      // 初始坐标
      const x3d = dirX * scaleFactor * radius
      const y3d = dirY * (scaleFactor * 0.9) * radius
      const z3d = dirZ * scaleFactor * radius

      const lifeSpan = 2000 + Math.random() * 3000 // 较快的生命周期，营造活跃感

      return {
        x: x3d,
        y: y3d,
        z: z3d,
        baseZ: z3d, // 原始深度，用于旋转
        baseX: x3d, // 原始水平位置
        speed: 0.02 + Math.random() * 0.04,
        tone,
        life: Math.random() * lifeSpan,
        lifeSpan,
        phase: Math.random() * Math.PI * 2
      }
    })

    // 预先建立随机连接关系
    const links = []
    for (let i = 0; i < NODE_COUNT; i++) {
      for (let j = i + 1; j < NODE_COUNT; j++) {
        if (Math.random() < 0.08) {
          links.push([i, j])
        }
      }
    }

    let time = 0

    function render() {
      ctx.clearRect(0, 0, width, height)
      time += 0.01

      const projected = []

      // Center
      const cx = width / 2
      const cy = height / 2

      // 整体缓慢旋转
      const rotSpeed = time * 0.5
      const cosR = Math.cos(rotSpeed)
      const sinR = Math.sin(rotSpeed)

      nodes.forEach(node => {
        // Update Life Cycle (呼吸感)
        node.life += 16
        if (node.life > node.lifeSpan) {
          node.life = 0
        }

        // 3D Rotation
        // 绕Y轴旋转
        const x = node.baseX * cosR - node.baseZ * sinR
        const z = node.baseZ * cosR + node.baseX * sinR
        const y = node.y + Math.sin(time * 2 + node.phase) * 2 // 轻微上下浮动

        // Project to 2D
        // Camera depth offset
        const zFinal = z + DEPTH
        const scale = 200 / (200 + zFinal)
        const x2d = cx + x * scale
        const y2d = cy + y * scale

        // Alpha based on life (fade in/out)
        const progress = node.life / node.lifeSpan
        const alphaLife = Math.sin(progress * Math.PI) // 0 -> 1 -> 0

        projected.push({ x: x2d, y: y2d, scale, tone: node.tone, alpha: alphaLife })
      })

      // Draw Links
      ctx.lineWidth = 1
      links.forEach(([i, j]) => {
        const n1 = projected[i]
        const n2 = projected[j]

        if (!n1 || !n2) return

        // 计算屏幕距离
        const dist = Math.hypot(n1.x - n2.x, n1.y - n2.y)
        const maxDist = scaleFactor * 1.5 // 允许稍远的连接

        if (dist < maxDist) {
          // 透明度 = 两端点透明度 * 距离衰减
          const op = Math.min(n1.alpha, n2.alpha) * (1 - dist / maxDist)
          // 首页配色：Emerald/Teal (99, 186, 154)
          ctx.strokeStyle = `rgba(99, 186, 154, ${op * 0.9})`
          ctx.beginPath()
          ctx.moveTo(n1.x, n1.y)
          ctx.lineTo(n2.x, n2.y)
          ctx.stroke()
        }
      })

      // Draw Nodes
      projected.forEach(p => {
        const r = p.scale * 2.5
        const glowRadius = r * 3 // 增加发光半径

        const gradient = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, glowRadius)
        const alpha = p.alpha * (0.3 + p.scale * 0.7)

        if (p.tone === 'dark') {
          gradient.addColorStop(0, `rgba(15, 23, 42, ${alpha})`)
          gradient.addColorStop(0.4, `rgba(15, 23, 42, ${alpha * 0.7})`)
          gradient.addColorStop(1, 'rgba(15, 23, 42, 0)')
        } else if (p.tone === 'light') {
          gradient.addColorStop(0, `rgba(248, 250, 252, ${alpha})`)
          gradient.addColorStop(0.4, `rgba(226, 232, 240, ${alpha * 0.9})`)
          gradient.addColorStop(1, 'rgba(248, 250, 252, 0)')
        } else {
          // "Color" tone matching Landing Page (Blueish Teal mix)
          // LandingPage gradient logic
          gradient.addColorStop(0, `rgba(140, 184, 255, ${alpha})`)
          gradient.addColorStop(0.35, `rgba(90, 166, 198, ${alpha})`)
          gradient.addColorStop(1, 'rgba(255, 255, 255, 0)')
        }

        ctx.fillStyle = gradient
        ctx.beginPath()
        ctx.arc(p.x, p.y, glowRadius, 0, Math.PI * 2)
        ctx.fill()
      })

      animationFrameId = requestAnimationFrame(render)
    }

    render()

    return () => cancelAnimationFrame(animationFrameId)
  }, [width, height])

  return (
    <Motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.5 }}
      style={{ width, height, display: 'flex', justifyContent: 'center', alignItems: 'center' }}
    >
      <canvas ref={canvasRef} />
    </Motion.div>
  )
}

export default MiniNeuralNetwork
