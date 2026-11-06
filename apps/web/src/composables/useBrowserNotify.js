/**
 * 浏览器系统通知。
 *
 * 后端在自动发货失败、账号掉线这类事件上会往 notification 表插一条记录
 * （automation_runtime.insert_notification）。前端此前只在导航面板列表里
 * 展示，从没消费成系统通知，所以「应用内通知」这一列一直是个没有任何
 * 效果的开关。
 *
 * 这里用轮询 + Notification API 把它接起来。首轮只建立基线不弹窗，避免
 * 登录瞬间把所有历史记录一次性弹出来。
 *
 * 限制：页面关闭后不会推送。要做到后台推送需要 Service Worker + Web Push
 * 与 VAPID 密钥，属于下一阶段。
 */

import { getNotifications } from '../api/notification.js'

const POLL_INTERVAL_MS = 30000

let timer = null
let lastSeenId = null
let started = false

export function notifySupported() {
  return typeof window !== 'undefined' && 'Notification' in window
}

export function notifyPermission() {
  if (!notifySupported()) return 'unsupported'
  return Notification.permission
}

export async function requestNotifyPermission() {
  if (!notifySupported()) return 'unsupported'
  if (Notification.permission === 'granted') return 'granted'
  if (Notification.permission === 'denied') return 'denied'
  try {
    return await Notification.requestPermission()
  } catch (_err) {
    return 'denied'
  }
}

function showNotification(item) {
  if (!notifySupported() || Notification.permission !== 'granted') return
  try {
    const body = String(item.content || '').replace(/\s+/g, ' ').slice(0, 160)
    const instance = new Notification(item.title || '应用内通知', {
      body,
      tag: `xya-notice-${item.id}`,
      icon: '/favicon.ico',
      badge: '/favicon.ico'
    })
    instance.onclick = () => {
      try {
        window.focus()
        window.location.hash = '#/messages'
      } finally {
        instance.close()
      }
    }
    setTimeout(() => {
      try { instance.close() } catch (_e) { /* 已关闭 */ }
    }, 15000)
  } catch (_err) {
    // 浏览器拒绝构造通知（例如非安全上下文），忽略即可
  }
}

async function pollOnce() {
  if (!notifySupported() || Notification.permission !== 'granted') return
  try {
    const res = await getNotifications({ current: 1, size: 10 })
    const records = res?.data?.records || []
    if (!records.length) return

    const maxId = records.reduce((acc, item) => Math.max(acc, Number(item.id) || 0), 0)
    if (lastSeenId === null) {
      lastSeenId = maxId
      return
    }

    const fresh = records
      .filter(item => Number(item.id) > lastSeenId && !item.readFlag)
      .sort((a, b) => Number(a.id) - Number(b.id))

    if (fresh.length) {
      lastSeenId = Math.max(lastSeenId, maxId)
      fresh.forEach(showNotification)
    }
  } catch (_err) {
    // 轮询失败不打扰用户，下一轮继续
  }
}

export function startBrowserNotify() {
  if (started || typeof window === 'undefined') return
  started = true
  lastSeenId = null
  void pollOnce()
  timer = window.setInterval(pollOnce, POLL_INTERVAL_MS)
}

export function stopBrowserNotify() {
  started = false
  lastSeenId = null
  if (timer) {
    window.clearInterval(timer)
    timer = null
  }
}
