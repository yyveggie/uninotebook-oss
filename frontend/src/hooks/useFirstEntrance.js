import { useEffect, useState } from 'react'

/**
 * 入场动画只在打开应用后的第一屏播放。
 *
 * 切换页面时旧页面立即卸载；新页面若从完全透明淡入，中间有零点几秒近乎空白，
 * 看起来就是整屏「闪一下」——即使是第一次进入某个页面也一样。
 * 打开应用时页面本来就在加载，第一屏淡入是加分项；之后切换页面一律直接显示。
 *
 * 同一次渲染中挂载的组件（如首页的标题、上传区、文档卡片）都会拿到 true，一起淡入。
 *
 * 用法：const animateIn = useFirstEntrance()
 *       <Motion.div initial={animateIn ? { opacity: 0, y: 12 } : false} animate={{ opacity: 1, y: 0 }} />
 */
let firstScreenShown = false

export default function useFirstEntrance() {
  const [animateIn] = useState(() => !firstScreenShown)
  useEffect(() => {
    firstScreenShown = true
  }, [])
  return animateIn
}
