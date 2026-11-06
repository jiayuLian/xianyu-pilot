<template>
  <aside class="sidebar" :class="{ open }">
    <button class="sidebar-close" type="button" aria-label="关闭菜单" @click="$emit('close')">
      <svg viewBox="0 0 24 24" class="ui-icon"><path d="M6 6l12 12M18 6 6 18" /></svg>
    </button>
    <button type="button" class="brand brand-image" aria-label="返回首页" @click="$emit('navigate','dashboard')">
      <img src="/xya/brand/brand_004.png" alt="XianYuAssistant 闲鱼助手" class="brand-logo" />
    </button>
    <nav class="nav-scroll">
      <div v-for="group in groups" :key="group.title" class="nav-group">
        <div class="nav-title">{{ group.title }}</div>
        <template v-for="item in group.items" :key="item.key">
          <button
            v-if="!item.children?.length"
            :class="['nav-item', { active: isActive(item.key) }]"
            @click="$emit('navigate', item.key)"
          >
            <span class="nav-icon"><Icon :name="item.icon" /></span>
            <span>{{ item.label }}</span>
            <span v-if="item.badge" class="nav-badge">{{ item.badge }}</span>
          </button>

          <template v-else>
            <button
              :class="['nav-item', 'has-children', { active: isActive(item.key), expanded: isExpanded(item) }]"
              :aria-expanded="isExpanded(item) ? 'true' : 'false'"
              @click="$emit('navigate', item.key)"
            >
              <span class="nav-icon"><Icon :name="item.icon" /></span>
              <span>{{ item.label }}</span>
              <span
                class="nav-caret"
                role="button"
                tabindex="0"
                :aria-label="(isExpanded(item) ? '收起' : '展开') + item.label"
                @click.stop="toggleGroup(item)"
                @keydown.enter.stop.prevent="toggleGroup(item)"
                @keydown.space.stop.prevent="toggleGroup(item)"
              >▾</span>
            </button>
            <button
              v-for="child in item.children"
              v-show="isExpanded(item)"
              :key="child.key"
              :class="['nav-item', 'child', { active: isActive(child.key) }]"
              @click="$emit('navigate', child.key)"
            >
              <span>{{ child.label }}</span>
            </button>
          </template>
        </template>
      </div>
    </nav>
    <button type="button" class="side-user" aria-label="打开个人中心" @click="$emit('navigate','profile')">
      <div class="avatar avatar-img"></div>
      <div class="side-user-main">
        <strong>{{ displayName }}</strong>
        <span>管理员</span>
      </div>
      <span class="online-dot"></span><span class="online-text">在线</span>
    </button>
    <button class="sidebar-logout" type="button" @click="$emit('logout')">退出登录</button>
    <div class="version">© {{ copyrightYear }} XianYuAssistant<br />v{{ APP_VERSION }}</div>
  </aside>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { navGroups } from '../data/nav.js'
import Icon from './Icon.vue'
import { APP_VERSION, getCopyrightYear } from '../utils/appMeta.js'
defineEmits(['navigate', 'close', 'logout'])
const props = defineProps({ active: { type: String, required: true }, user: { type: Object, default: () => ({}) }, open: { type: Boolean, default: false } })

// 子账号管理只有超级管理员能用。标了 superOnly 的条目对 operator / viewer
// 直接不下发，避免点进去只看到 403。后端仍会兜底校验，这里只是体验层。
const isSuper = computed(() => {
  const raw = String(props.user?.role || '').toLowerCase()
  return raw === 'super' || raw === 'admin' || raw === 'root' || raw === ''
})

const groups = computed(() => {
  if (isSuper.value) return navGroups
  return navGroups
    .map(group => ({ ...group, items: group.items.filter(item => !item.superOnly) }))
    .filter(group => group.items.length > 0)
})

// 二级菜单展开状态：用户没手动点过时，按「当前页是否在该组里」决定，
// 手动点过就记住，刷新后保持。存 key -> bool 的映射，避免用数组时
// 「从未操作」和「主动收起」两种状态分不开。
const EXPAND_STORAGE_KEY = 'xya.nav.expanded'

function readExpandMap() {
  try {
    const raw = globalThis.localStorage?.getItem(EXPAND_STORAGE_KEY)
    const parsed = raw ? JSON.parse(raw) : null
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {}
  } catch {
    return {}
  }
}

const expandMap = ref(readExpandMap())

function isExpanded(item) {
  if (!item?.children?.length) return false
  const saved = expandMap.value[item.key]
  if (typeof saved === 'boolean') return saved
  return item.children.some(child => child.key === props.active)
}

function toggleGroup(item) {
  const next = !isExpanded(item)
  expandMap.value = { ...expandMap.value, [item.key]: next }
  try {
    globalThis.localStorage?.setItem(EXPAND_STORAGE_KEY, JSON.stringify(expandMap.value))
  } catch {
    // 隐私模式下不可写，本次会话内仍按内存状态生效
  }
}

// 从别处跳到子页面（如首页快捷入口）时，把所属分组自动展开
watch(() => props.active, current => {
  for (const group of navGroups) {
    for (const item of group.items) {
      if (!item.children?.some(child => child.key === current)) continue
      if (expandMap.value[item.key] === false) {
        expandMap.value = { ...expandMap.value, [item.key]: true }
      }
    }
  }
})

const displayName = computed(() => props.user?.username || props.user?.displayName || props.user?.name || '管理员')
const copyrightYear = getCopyrightYear()
function isActive(key) {
  if (props.active.startsWith('settings-') && props.active !== 'settings-notify' && key === 'settings-system') return true
  return props.active === key
}
</script>
