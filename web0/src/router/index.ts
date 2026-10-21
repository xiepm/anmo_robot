/** 应用路由与流程守卫 */
import { createRouter, createWebHistory } from 'vue-router'
import { useFlowStore } from '@/stores/flow'
import { useToastStore } from '@/stores/toast'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      component: () => import('@/layouts/WorkbenchLayout.vue'),
      children: [
        {
          path: '',
          name: 'service-start',
          meta: { crumb: '服务首页' },
          component: () => import('@/views/service/StartView.vue'),
        },
        {
          path: 'survey',
          name: 'service-survey',
          meta: { crumb: '服务问卷' },
          component: () => import('@/views/service/SurveyView.vue'),
        },
        {
          path: 'plan',
          name: 'service-plan',
          meta: { crumb: '方案确认' },
          component: () => import('@/views/service/PlanView.vue'),
        },
        {
          path: 'position',
          name: 'service-position',
          meta: { crumb: '人体定位' },
          component: () => import('@/views/service/PositionView.vue'),
        },
        {
          path: 'scan',
          name: 'service-scan',
          meta: { crumb: '扫描中' },
          component: () => import('@/views/service/ScanView.vue'),
        },
        {
          path: 'confirm',
          name: 'service-confirm',
          meta: { crumb: '路线与参数' },
          component: () => import('@/views/service/ConfirmView.vue'),
        },
        {
          path: 'run',
          name: 'service-run',
          meta: { crumb: '运行监控' },
          component: () => import('@/views/service/RunView.vue'),
        },
        {
          path: 'abnormal',
          name: 'service-abnormal',
          meta: { crumb: '安全异常' },
          component: () => import('@/views/service/AbnormalView.vue'),
        },
        {
          path: 'finish',
          name: 'service-finish',
          meta: { crumb: '完成归档' },
          component: () => import('@/views/service/FinishView.vue'),
        },
        {
          path: 'records',
          name: 'service-records',
          meta: { crumb: '服务记录' },
          component: () => import('@/views/service/RecordsView.vue'),
        },
      ],
    },
  ],
})

router.beforeEach((to) => {
  const name = typeof to.name === 'string' ? to.name : ''
  if (!name.startsWith('service-')) return true

  const flow = useFlowStore()
  flow.syncStageByRoute(name)

  if (name === 'service-records' || name === 'service-start') return true
  if (flow.canEnter(name)) return true

  const toast = useToastStore()
  toast.show(flow.lockedMessage(name))
  return { name: 'service-start' }
})

export default router
