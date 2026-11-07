import React, { useEffect, useRef } from 'react'
import * as d3 from 'd3'
import './MindMap.css'

/**
 * 思维导图组件
 * 使用 D3.js 渲染树状思维导图
 */
const MindMap = ({ data, onNodeClick, expandingNodeId }) => {
  const svgRef = useRef(null)
  const containerRef = useRef(null)
  const currentTransformRef = useRef(null)  // 保存当前的缩放和位置
  const isInitialLoadRef = useRef(true)     // 标记是否是初始加载

  useEffect(() => {
    if (!data || !data.root) return

    // 清空之前的内容
    d3.select(svgRef.current).selectAll('*').remove()

    // 获取容器尺寸
    const container = containerRef.current
    const width = container.clientWidth
    const height = container.clientHeight

    // 创建 SVG - 简化viewBox设置
    const svg = d3.select(svgRef.current)
      .attr('width', width)
      .attr('height', height)
      .attr('viewBox', `0 0 ${width} ${height}`)  // 标准viewBox，从(0,0)开始

    // 创建缩放和平移容器
    const g = svg.append('g')

    // 添加缩放功能（移除边界限制）
    const zoom = d3.zoom()
      .scaleExtent([0.2, 4])
      .on('zoom', (event) => {
        g.attr('transform', event.transform)
        // 保存当前的transform状态
        currentTransformRef.current = event.transform
      })

    svg.call(zoom)

    // 转换数据格式
    const root = d3.hierarchy(data.root, d => d.children)

    // 计算节点总数
    const nodeCount = root.descendants().length
    
    // 使用固定的大布局空间，不限制节点展开
    const layoutHeight = nodeCount * 60  // 每个节点60px垂直空间
    const layoutWidth = 1200  // 固定水平空间

    // 创建树布局 - 增加节点间距
    const treeLayout = d3.tree()
      .size([layoutHeight, layoutWidth])
      .separation((a, b) => (a.parent === b.parent ? 2.5 : 3))

    // 应用布局
    treeLayout(root)

    // 计算所有节点的边界
    const allNodes = root.descendants()
    const xValues = allNodes.map(d => d.x)
    const yValues = allNodes.map(d => d.y)
    const minX = Math.min(...xValues)
    const maxX = Math.max(...xValues)
    const minY = Math.min(...yValues)
    const maxY = Math.max(...yValues)
    
    // 计算实际内容的尺寸
    const contentHeight = maxX - minX
    const contentWidth = maxY - minY + 600  // 加上文本和按钮的空间（增加到600）
    
    // 将所有节点坐标归一化到从(0,0)开始
    allNodes.forEach(d => {
      d.x = d.x - minX  // 垂直坐标归零
      d.y = d.y - minY  // 水平坐标归零
    })

    // 绘制连接线（坐标已归一化，不需要offset）
    g.selectAll('.link')
      .data(root.links())
      .enter()
      .append('path')
      .attr('class', 'link')
      .attr('d', d => {
        const sourceX = d.source.y
        const sourceY = d.source.x
        const targetX = d.target.y
        const targetY = d.target.x
        
        // 使用贝塞尔曲线
        return `M${sourceX},${sourceY}C${(sourceX + targetX) / 2},${sourceY} ${(sourceX + targetX) / 2},${targetY} ${targetX},${targetY}`
      })

    // 创建节点组（坐标已归一化，不需要offset）
    const nodes = g.selectAll('.node')
      .data(root.descendants())
      .enter()
      .append('g')
      .attr('class', d => `node ${d.data.id}`)
      .attr('transform', d => `translate(${d.y},${d.x})`)

    // 添加节点圆圈
    nodes.append('circle')
      .attr('r', d => d.depth === 0 ? 8 : 5)
      .attr('class', d => {
        if (d.depth === 0) return 'node-circle root'
        if (d.depth === 1) return 'node-circle level-1'
        if (d.depth === 2) return 'node-circle level-2'
        return 'node-circle level-3'
      })

    // 添加节点文本背景 - 统一文本在右侧
    nodes.each(function(d) {
      const node = d3.select(this)
      const text = node.append('text')
        .attr('dy', '.35em')
        .attr('x', 15)  // 统一：所有文本都在圆圈右侧
        .attr('text-anchor', 'start')  // 统一：左对齐
        .attr('class', 'node-text')
        .text(d.data.title)

      // 获取文本边界
      const bbox = text.node().getBBox()
      
      // 在文本后面添加背景矩形
      node.insert('rect', 'text')
        .attr('x', bbox.x - 4)
        .attr('y', bbox.y - 2)
        .attr('width', bbox.width + 8)
        .attr('height', bbox.height + 4)
        .attr('class', 'node-text-bg')
    })

    // 添加描述信息（如果有）- 暂时禁用，避免显示问题
    // nodes.each(function(d) {
    //   if (d.data.description && d.data.description.trim()) {
    //     const node = d3.select(this)
    //     
    //     // 添加描述文本
    //     node.append('text')
    //       .attr('dy', '1.8em')
    //       .attr('x', d => d.depth === 0 ? 15 : (d.children ? -10 : 10))
    //       .attr('text-anchor', d => d.depth === 0 ? 'start' : (d.children ? 'end' : 'start'))
    //       .attr('class', 'node-description')
    //       .text(d.data.description.length > 30 ? d.data.description.substring(0, 30) + '...' : d.data.description)
    //   }
    // })

    // 为每个节点添加扩展按钮 - 放在文本右侧
    nodes.each(function(d) {
      const node = d3.select(this)
      const isExpanding = expandingNodeId === d.data.id
      
      // 获取文本宽度，按钮放在文本右侧
      const textElement = node.select('.node-text').node()
      const textBBox = textElement ? textElement.getBBox() : { x: 15, width: 0 }
      
      // 按钮位置：文本右侧 + 更大的间距（避免缩放后重叠）
      const buttonOffsetX = textBBox.x + textBBox.width + 25  // 文本右边 + 25px间距（增加）
      const buttonOffsetY = 0  // 垂直对齐文本
      
      // 创建按钮组
      const buttonGroup = node.append('g')
        .attr('class', 'expand-button-group')
        .attr('transform', `translate(${buttonOffsetX}, ${buttonOffsetY})`)
        .style('cursor', isExpanding ? 'wait' : 'pointer')
        .style('opacity', 1)  // 默认显示
        .on('click', function(event) {
          event.stopPropagation()
          if (onNodeClick && !isExpanding) {
            onNodeClick(d.data)
          }
        })
      
      // 添加按钮背景圆圈 - 稍微缩小
      buttonGroup.append('circle')
        .attr('r', 9)  // 从10减少到9
        .attr('class', isExpanding ? 'expand-button expanding' : 'expand-button')
        .attr('fill', isExpanding ? '#FFA726' : '#4A90E2')
        .attr('stroke', 'white')
        .attr('stroke-width', 1.5)  // 从2减少到1.5
      
      // 添加按钮图标 - 稍微缩小字体
      if (isExpanding) {
        // 加载中的旋转动画
        buttonGroup.append('text')
          .attr('class', 'expand-icon spinning')
          .attr('text-anchor', 'middle')
          .attr('dy', '0.35em')
          .attr('fill', 'white')
          .attr('font-size', '11px')  // 从12减少到11
          .text('⏳')
      } else {
        // 扩展图标
        buttonGroup.append('text')
          .attr('class', 'expand-icon')
          .attr('text-anchor', 'middle')
          .attr('dy', '0.35em')
          .attr('fill', 'white')
          .attr('font-size', '13px')  // 从14减少到13
          .attr('font-weight', 'bold')
          .text('+')
      }
    })

    // 只在初次加载时计算初始缩放，之后保持用户的视图状态
    if (isInitialLoadRef.current) {
      // 计算合适的初始缩放比例和位置
      const padding = 50  // 边距
      const scaleX = (width - padding * 2) / contentWidth
      const scaleY = (height - padding * 2) / contentHeight
      const initialScale = Math.min(scaleX, scaleY, 1) * 0.95  // 缩放到适合画布，最多95%

      // 计算居中位置
      const scaledWidth = contentWidth * initialScale
      const scaledHeight = contentHeight * initialScale
      const translateX = (width - scaledWidth) / 2
      const translateY = (height - scaledHeight) / 2

      const initialTransform = d3.zoomIdentity
        .translate(translateX, translateY)
        .scale(initialScale)

      svg.call(zoom.transform, initialTransform)
      currentTransformRef.current = initialTransform
      isInitialLoadRef.current = false
    } else {
      // 非初次加载，保持当前的视图状态
      if (currentTransformRef.current) {
        svg.call(zoom.transform, currentTransformRef.current)
      }
    }
    
    // 日志已移动到上面的if分支中

  }, [data, expandingNodeId, onNodeClick])

  return (
    <div className="mindmap-wrapper" ref={containerRef}>
      <svg ref={svgRef}></svg>
      <div className="mindmap-controls">
        <p className="mindmap-hint">💡 拖动和缩放查看 | 🖱️ 点击蓝色按钮扩展节点</p>
      </div>
    </div>
  )
}

export default MindMap

