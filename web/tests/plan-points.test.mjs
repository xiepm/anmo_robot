import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'
const source = await readFile(new URL('../src/data/points.ts', import.meta.url), 'utf8')
const output = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText
const { pointSets, toProtocolPoint, selectRecognizedPoints } = await import(`data:text/javascript;base64,${Buffer.from(output).toString('base64')}`)
const planSource = await readFile(new URL('../src/data/plans.ts', import.meta.url), 'utf8')
const planOutput = ts.transpileModule(planSource, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText
const { servicePlans } = await import(`data:text/javascript;base64,${Buffer.from(planOutput).toString('base64')}`)
const catalogue = JSON.parse(await readFile(new URL('../../config/rehab_protocol/acupoints.json', import.meta.url), 'utf8'))
const available = catalogue.regions.BACK.flatMap(p => p.sides.map(side => ({ name: p.name, side })))
test('every selectable plan has a complete unique route in the shared catalogue', () => {
  assert.equal(servicePlans.length, 4)
  for (const { id, region } of servicePlans) {
    const route = pointSets[id].map(([label]) => toProtocolPoint(label))
    const recognized = catalogue.regions[region].flatMap(p => p.sides.map(side => ({ name: p.name, side })))
    assert.ok(route.length > 0, id)
    assert.equal(new Set(route.map(p => `${p.side}:${p.name}`)).size, route.length, id)
    assert.deepEqual(selectRecognizedPoints(route, recognized.reverse()), { recognized: route, missing: [] }, id)
  }
})
test('waist massage uses the six selected named points without aliasing a region', () => {
  const route = pointSets.p4.map(([label]) => toProtocolPoint(label))
  assert.deepEqual(route, [
    { name: '命门', side: 'MIDLINE' },
    { name: '肾俞', side: 'LEFT' },
    { name: '肾俞', side: 'RIGHT' },
    { name: '腰阳关', side: 'MIDLINE' },
    { name: '大肠俞', side: 'LEFT' },
    { name: '大肠俞', side: 'RIGHT' },
  ])
  const missing = route[5]
  const measured = available.filter(p => p.name !== missing.name || p.side !== missing.side)
  assert.deepEqual(selectRecognizedPoints(route, measured), { recognized: route.slice(0, 5), missing: [missing] })
  const region = [{ name: '腰背', side: 'LEFT' }, { name: '腰背', side: 'RIGHT' }]
  assert.deepEqual(selectRecognizedPoints(region, available), { recognized: [], missing: region })
})
test('photo matching uses both name and side, drops unrelated points, and retains route order', () => {
  const route = pointSets.p1.map(([label]) => toProtocolPoint(label))
  const measured = [route[2], { name: '肾俞', side: 'LEFT' }, route[0]]
  assert.deepEqual(selectRecognizedPoints(route, measured), { recognized: [route[0], route[2]], missing: [route[1]] })
  assert.deepEqual(selectRecognizedPoints(route, []), { recognized: [], missing: route })
})
