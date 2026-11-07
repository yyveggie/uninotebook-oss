import fs from 'node:fs'
import path from 'node:path'
import zlib from 'node:zlib'

const KB = 1024

const DEFAULT_BUDGETS = {
	entryJsGzipKb: 300,
	entryCssGzipKb: 120,
	totalJsGzipKb: 1500,
	totalCssGzipKb: 220,
}

const readNumber = (envName, fallback) => {
	const value = Number(process.env[envName])
	return Number.isFinite(value) && value > 0 ? value : fallback
}

const budgets = {
	entryJsGzipKb: readNumber('BUDGET_ENTRY_JS_GZIP_KB', DEFAULT_BUDGETS.entryJsGzipKb),
	entryCssGzipKb: readNumber('BUDGET_ENTRY_CSS_GZIP_KB', DEFAULT_BUDGETS.entryCssGzipKb),
	totalJsGzipKb: readNumber('BUDGET_TOTAL_JS_GZIP_KB', DEFAULT_BUDGETS.totalJsGzipKb),
	totalCssGzipKb: readNumber('BUDGET_TOTAL_CSS_GZIP_KB', DEFAULT_BUDGETS.totalCssGzipKb),
}

const projectRoot = process.cwd()
const distDir = path.join(projectRoot, 'dist')
const assetsDir = path.join(distDir, 'assets')
const indexHtmlPath = path.join(distDir, 'index.html')

if (!fs.existsSync(indexHtmlPath) || !fs.existsSync(assetsDir)) {
	console.error('❌ 未检测到 dist 构建产物，请先执行 npm run build')
	process.exit(1)
}

const toGzipKb = (filePath) => {
	const content = fs.readFileSync(filePath)
	return zlib.gzipSync(content, { level: 9 }).length / KB
}

const indexHtml = fs.readFileSync(indexHtmlPath, 'utf8')
const entryJsMatches = [...indexHtml.matchAll(/<script[^>]+src="\/assets\/([^"]+\.js)"/g)]
const entryCssMatches = [...indexHtml.matchAll(/<link[^>]+href="\/assets\/([^"]+\.css)"/g)]

const uniq = (arr) => [...new Set(arr)]

const entryJsFiles = uniq(entryJsMatches.map((match) => match[1]))
const entryCssFiles = uniq(entryCssMatches.map((match) => match[1]))

const allAssetFiles = fs.readdirSync(assetsDir)
const allJsFiles = allAssetFiles.filter((name) => name.endsWith('.js'))
const allCssFiles = allAssetFiles.filter((name) => name.endsWith('.css'))

const sumGzipKb = (files) => files.reduce((sum, file) => sum + toGzipKb(path.join(assetsDir, file)), 0)

const metrics = {
	entryJsGzipKb: sumGzipKb(entryJsFiles),
	entryCssGzipKb: sumGzipKb(entryCssFiles),
	totalJsGzipKb: sumGzipKb(allJsFiles),
	totalCssGzipKb: sumGzipKb(allCssFiles),
}

const checks = [
	['entryJsGzipKb', '入口 JS(gzip)'],
	['entryCssGzipKb', '入口 CSS(gzip)'],
	['totalJsGzipKb', '总 JS(gzip)'],
	['totalCssGzipKb', '总 CSS(gzip)'],
]

let hasFailure = false

console.log('📦 Bundle Budget Report (KB, gzip):')

for (const [key, label] of checks) {
	const value = metrics[key]
	const limit = budgets[key]
	const passed = value <= limit
	const status = passed ? '✅' : '❌'
	if (!passed) {
		hasFailure = true
	}
	console.log(`${status} ${label}: ${value.toFixed(2)} / ${limit.toFixed(2)}`)
}

if (hasFailure) {
	console.error('\n🚫 Bundle 体积超预算，请优化后再合并。')
	process.exit(1)
}

console.log('\n✅ Bundle 预算校验通过。')
