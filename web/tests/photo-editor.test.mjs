import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'
const source = await readFile(new URL('../src/utils/photoEditor.ts', import.meta.url), 'utf8')
const output = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText
const { parsePhotoData, clampPixel, routeSegments } = await import(`data:text/javascript;base64,${Buffer.from(output).toString('base64')}`)
const a = { name: '肩井', side: 'LEFT', x: 100, y: 120, coordinate_valid: true }
const b = { ...a, side: 'RIGHT', x: 300 }
const c = { ...a, name: '大椎', side: 'MIDLINE', x: 200 }
const capture = { frame_number: 12, received_monotonic_s: 4.5, available_points: [a, b] }
const photo = () => ({ frame_number: 12, received_monotonic_s: 4.5, width: 640, height: 480, points: [a, b] })
test('photo identity, finite pixel bounds, unique sides and recognition validity are enforced', () => {
  assert.equal(parsePhotoData(photo(), capture).points.length, 2)
  for (const patch of [{ frame_number: 13 }, { received_monotonic_s: 4.6 }, { width: 0 },
    { points: [a, a] }, { points: [{ ...a, x: NaN }, b] }, { points: [{ ...a, x: 640 }, b] },
    { points: [a] }, { points: [a, { ...b, coordinate_valid: false }] }]) {
    assert.throws(() => parsePhotoData({ ...photo(), ...patch }, capture))
  }
})
test('drag coordinates clamp to the actual photo, with subpixel precision', () => {
  assert.deepEqual(clampPixel(-12, 999, 640, 480), { x: 0, y: 479 })
  assert.deepEqual(clampPixel(20.24, 77.76, 640, 480), { x: 20.2, y: 77.8 })
})
test('route follows ordered points and moves with them without bridging a missing point', () => {
  assert.deepEqual(routeSegments([a, b, c]), [{ from: a, to: b }, { from: b, to: c }])
  assert.deepEqual(routeSegments([a, null, c]), [])
  const moved = { ...b, x: 305, y: 130 }
  assert.deepEqual(routeSegments([a, moved, c]), [{ from: a, to: moved }, { from: moved, to: c }])
  assert.equal(b.x, 300)
})
