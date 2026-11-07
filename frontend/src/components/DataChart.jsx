import { useMemo } from 'react'
import {
    ResponsiveContainer,
    BarChart,
    Bar,
    LineChart,
    Line,
    AreaChart,
    Area,
    PieChart,
    Pie,
    Cell,
    XAxis,
    YAxis,
    CartesianGrid,
    Tooltip,
    Legend
} from 'recharts'

/**
 * 通用数据图表组件
 * @param {Object} props
 * @param {'bar'|'line'|'area'|'pie'} props.type - 图表类型
 * @param {Array<Object>} props.data - 数据数组 [ { name: '2020', value: 100 }, ... ]
 * @param {string} props.title - 图表标题
 * @param {string} props.xKey - X轴字段名 (默认 'name')
 * @param {Array<string>} props.dataKeys - 数据字段名数组 (默认 ['value'])
 * @param {Array<string>} props.colors - 颜色数组
 * @param {string} props.unit - 单位 (显示在 Tooltip 中)
 */
const DataChart = ({
    type = 'bar',
    data = [],
    xKey = 'name',
    dataKeys = ['value'],
    colors = [
        '#6366f1', // Indigo
        '#ec4899', // Pink
        '#10b981', // Emerald
        '#f59e0b', // Amber
        '#3b82f6', // Blue
        '#8b5cf6', // Violet
        '#ef4444', // Red
        '#14b8a6', // Teal
        '#f97316', // Orange
        '#84cc16', // Lime
        '#06b6d4', // Cyan
        '#d946ef'  // Fuchsia
    ],
    unit = ''
}) => {
    // 计算标签是否需要倾斜（当标签较长或数据较多时）
    const needsAngle = useMemo(() => {
        if (!data || data.length === 0) return false
        const maxLabelLength = Math.max(...data.map(d => String(d[xKey] || '').length))
        // 如果标签超过8个字符或数据超过5条，使用倾斜标签
        return maxLabelLength > 8 || data.length > 5
    }, [data, xKey])

    // 确保数据有效
    if (!data || data.length === 0) {
        return null
    }

    // 自定义 X 轴标签渲染（支持倾斜）
    const CustomXAxisTick = ({ x, y, payload }) => {
        const label = String(payload.value || '')
        // 截断过长的标签
        const displayLabel = label.length > 15 ? label.slice(0, 12) + '...' : label

        if (needsAngle) {
            return (
                <g transform={`translate(${x},${y})`}>
                    <text
                        x={0}
                        y={0}
                        dy={8}
                        textAnchor="end"
                        fill="#64748b"
                        fontSize={11}
                        transform="rotate(-35)"
                    >
                        {displayLabel}
                    </text>
                </g>
            )
        }

        return (
            <g transform={`translate(${x},${y})`}>
                <text
                    x={0}
                    y={0}
                    dy={12}
                    textAnchor="middle"
                    fill="#64748b"
                    fontSize={12}
                >
                    {displayLabel}
                </text>
            </g>
        )
    }

    // 自定义 Legend 渲染（水平排列）
    const renderLegend = (props) => {
        const { payload } = props
        return (
            <div style={{
                display: 'flex',
                flexDirection: 'row',
                flexWrap: 'wrap',
                justifyContent: 'center',
                gap: '2px 16px',
                paddingTop: '2px',
                marginTop: '-4px'
            }}>
                {payload.map((entry, index) => (
                    <div key={`legend-${index}`} style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '6px'
                    }}>
                        <span style={{
                            width: '10px',
                            height: '10px',
                            backgroundColor: entry.color,
                            borderRadius: '2px',
                            flexShrink: 0
                        }} />
                        <span style={{ color: entry.color, fontSize: '13px' }}>
                            {entry.value}
                        </span>
                    </div>
                ))}
            </div>
        )
    }

    // 自定义 Tooltip
    const CustomTooltip = ({ active, payload, label }) => {
        if (active && payload && payload.length) {
            return (
                <div style={{
                    backgroundColor: 'rgba(255, 255, 255, 0.96)',
                    border: '1px solid #e2e8f0',
                    borderRadius: '8px',
                    padding: '12px',
                    boxShadow: '0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05)',
                    minWidth: '120px',
                    zIndex: 50
                }}>
                    <p style={{
                        margin: '0 0 8px 0',
                        fontWeight: 600,
                        color: '#1e293b',
                        fontSize: '14px',
                        lineHeight: '1.4'
                    }}>{label}</p>
                    {payload.map((entry, index) => (
                        <div key={index} style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: '8px',
                            marginBottom: '4px',
                            fontSize: '13px',
                            color: '#475569'
                        }}>
                            <span style={{
                                width: '8px',
                                height: '8px',
                                borderRadius: '50%',
                                backgroundColor: entry.color,
                                display: 'inline-block',
                                flexShrink: 0
                            }}></span>
                            <span style={{ flex: 1 }}>{entry.name}:</span>
                            <span style={{
                                color: '#0f172a',
                                fontWeight: 600,
                                fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace'
                            }}>
                                {entry.value} {unit}
                            </span>
                        </div>
                    ))}
                </div>
            )
        }
        return null
    }

    // 渲染图表主体
    const renderChart = () => {
        // 根据是否需要倾斜标签调整底部边距
        const bottomMargin = needsAngle ? 50 : 5
        const commonProps = {
            data,
            margin: { top: 10, right: 30, left: 0, bottom: bottomMargin }
        }

        switch (type) {
            case 'line':
                return (
                    <LineChart {...commonProps}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                        <XAxis
                            dataKey={xKey}
                            tick={<CustomXAxisTick />}
                            axisLine={{ stroke: '#cbd5e1' }}
                            tickLine={false}
                            interval={0}
                            height={needsAngle ? 60 : 30}
                        />
                        <YAxis tick={{ fill: '#64748b', fontSize: 12 }} axisLine={false} tickLine={false} />
                        <Tooltip content={<CustomTooltip />} cursor={{ stroke: '#94a3b8', strokeWidth: 1, strokeDasharray: '3 3' }} />
                        <Legend
                            content={renderLegend}
                            wrapperStyle={{ paddingTop: 0, marginTop: -10 }}
                        />
                        {dataKeys.map((key, index) => (
                            <Line
                                key={key}
                                type="monotone"
                                dataKey={key}
                                stroke={colors[index % colors.length]}
                                strokeWidth={3}
                                dot={{ r: 4, strokeWidth: 2, fill: '#fff' }}
                                activeDot={{ r: 6, strokeWidth: 0 }}
                            />
                        ))}
                    </LineChart>
                )

            case 'area':
                return (
                    <AreaChart {...commonProps}>
                        <defs>
                            {dataKeys.map((key, index) => (
                                <linearGradient key={key} id={`color${key}`} x1="0" y1="0" x2="0" y2="1">
                                    <stop offset="5%" stopColor={colors[index % colors.length]} stopOpacity={0.3} />
                                    <stop offset="95%" stopColor={colors[index % colors.length]} stopOpacity={0} />
                                </linearGradient>
                            ))}
                        </defs>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                        <XAxis
                            dataKey={xKey}
                            tick={<CustomXAxisTick />}
                            axisLine={{ stroke: '#cbd5e1' }}
                            tickLine={false}
                            interval={0}
                            height={needsAngle ? 60 : 30}
                        />
                        <YAxis tick={{ fill: '#64748b', fontSize: 12 }} axisLine={false} tickLine={false} />
                        <Tooltip content={<CustomTooltip />} />
                        <Legend
                            content={renderLegend}
                            wrapperStyle={{ paddingTop: 0, marginTop: -10 }}
                        />
                        {dataKeys.map((key, index) => (
                            <Area
                                key={key}
                                type="monotone"
                                dataKey={key}
                                stroke={colors[index % colors.length]}
                                fillOpacity={1}
                                fill={`url(#color${key})`}
                                strokeWidth={3}
                            />
                        ))}
                    </AreaChart>
                )

            case 'pie':
                return (
                    <PieChart>
                        <Pie
                            data={data}
                            cx="50%"
                            cy="50%"
                            innerRadius={60}
                            outerRadius={80}
                            paddingAngle={5}
                            dataKey={dataKeys[0]} // 饼图只支持单维度
                            nameKey={xKey}
                        >
                            {data.map((entry, index) => (
                                <Cell key={`cell-${index}`} fill={colors[index % colors.length]} stroke="none" />
                            ))}
                        </Pie>
                        <Tooltip content={<CustomTooltip />} />
                        <Legend
                            layout="vertical"
                            verticalAlign="middle"
                            align="right"
                            wrapperStyle={{ paddingLeft: '20px' }}
                        />
                    </PieChart>
                )

            case 'bar':
            default:
                return (
                    <BarChart {...commonProps}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                        <XAxis
                            dataKey={xKey}
                            tick={<CustomXAxisTick />}
                            axisLine={{ stroke: '#cbd5e1' }}
                            tickLine={false}
                            interval={0}
                            height={needsAngle ? 60 : 30}
                        />
                        <YAxis tick={{ fill: '#64748b', fontSize: 12 }} axisLine={false} tickLine={false} />
                        <Tooltip content={<CustomTooltip />} cursor={{ fill: '#f1f5f9' }} />
                        <Legend
                            content={renderLegend}
                            wrapperStyle={{ paddingTop: 0, marginTop: -10 }}
                        />
                        {dataKeys.map((key, index) => (
                            <Bar
                                key={key}
                                dataKey={key}
                                fill={colors[index % colors.length]}
                                radius={[4, 4, 0, 0]}
                                barSize={data.length > 5 ? 30 : 50}
                            />
                        ))}
                    </BarChart>
                )
        }
    }

    return (
        <div style={{ width: '100%', height: 260 }}>
            <ResponsiveContainer textAnchor="start">
                {renderChart()}
            </ResponsiveContainer>
        </div>
    )
}

export default DataChart
