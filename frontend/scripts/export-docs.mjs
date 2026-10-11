import { chromium } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'

const output = resolve(process.argv[2] || '../docs/frontend/scepka-guide.pdf')
const browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE })
try {
  const page = await browser.newPage({ locale: 'ru-RU', colorScheme: 'light' })
  await page.goto(`${process.env.SCEPKA_BASE_URL || 'http://localhost:3000'}/docs?bundle=frontend`)
  await page.waitForSelector('.document-section .markdown[data-ready="true"]')
  await page.waitForFunction(() => [...document.querySelectorAll('.markdown')].length > 0 && [...document.querySelectorAll('.markdown')].every(node => node.getAttribute('data-ready') === 'true'))
  await page.evaluate(() => document.fonts.ready)
  const errors = await page.getByRole('alert').allTextContents()
  if (errors.length) throw new Error(errors.join('\n'))
  await mkdir(dirname(output), { recursive: true })
  await page.pdf({ path: output, format: 'A4', printBackground: true, displayHeaderFooter: true,
    headerTemplate: '<span></span>', footerTemplate: '<div style="font-family:monospace;font-size:9px;width:100%;text-align:center"><span class="pageNumber"></span> / <span class="totalPages"></span></div>',
    margin: { top: '18mm', right: '18mm', bottom: '18mm', left: '18mm' } })
  console.log(output)
} finally { await browser.close() }
