<template>
  <div class="au-page">
    <div v-if="notice.text" :class="['au-notice', notice.type]">{{ notice.text }}</div>

    <header class="au-head">
      <div class="au-head-copy">
        <h2>子账号管理</h2>
        <p>超级管理员可创建下属账号并限制其可操作的资源范围。当前登录：<strong>{{ currentName }}</strong>（{{ currentRoleLabel }}）</p>
      </div>
      <button type="button" class="au-btn au-btn-primary" :disabled="loading" @click="openCreate">
        + 新建子账号
      </button>
    </header>

    <section class="au-card">
      <div v-if="loading" class="au-empty">加载中…</div>
      <div v-else-if="!users.length" class="au-empty">还没有子账号，点右上角新建。</div>
      <table v-else class="au-table">
        <thead>
          <tr>
            <th>用户名</th>
            <th>角色</th>
            <th>状态</th>
            <th>账号配额</th>
            <th>最后登录</th>
            <th class="right">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in users" :key="row.id">
            <td>
              <div class="au-name">{{ row.username }}</div>
              <div class="au-sub">{{ row.email || '未填邮箱' }}</div>
            </td>
            <td><span :class="['au-tag', 'role-' + row.role]">{{ roleLabel(row.role) }}</span></td>
            <td>
              <span :class="['au-tag', row.status ? 'is-on' : 'is-off']">
                {{ row.status ? '启用' : '停用' }}
              </span>
            </td>
            <td>{{ row.accountLimit === null || row.accountLimit === undefined ? '不限' : row.accountLimit }}</td>
            <td>
              <div>{{ row.lastLoginTime || '从未登录' }}</div>
              <div class="au-sub">{{ row.lastLoginIp || '' }}</div>
            </td>
            <td class="right">
              <button type="button" class="au-link" @click="openEdit(row)">编辑</button>
              <button type="button" class="au-link" @click="openPermissions(row)">权限</button>
              <button type="button" class="au-link" @click="openReset(row)">改密</button>
              <button
                type="button"
                class="au-link danger"
                :disabled="row.role === 'super'"
                @click="removeUser(row)"
              >删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- 新建 / 编辑 -->
    <div v-if="formOpen" class="au-mask" @click.self="formOpen = false">
      <div class="au-modal">
        <h3>{{ formMode === 'create' ? '新建子账号' : '编辑账号' }}</h3>

        <label class="au-field">
          <span>用户名</span>
          <input v-model.trim="form.username" :disabled="formMode === 'edit'" placeholder="3 个字符以上" />
        </label>

        <label v-if="formMode === 'create'" class="au-field">
          <span>初始密码</span>
          <input v-model="form.password" type="password" placeholder="至少 8 位，含字母与数字" />
        </label>

        <label class="au-field">
          <span>角色</span>
          <select v-model="form.role">
            <option value="operator">操作员（可写）</option>
            <option value="viewer">只读</option>
          </select>
        </label>

        <label class="au-field">
          <span>邮箱（可选）</span>
          <input v-model.trim="form.email" type="email" placeholder="用于通知与找回" />
        </label>

        <label class="au-field">
          <span>可绑定闲鱼账号数</span>
          <input v-model.number="form.accountLimit" type="number" min="1" placeholder="留空表示不限" />
        </label>

        <label v-if="formMode === 'edit'" class="au-field">
          <span>状态</span>
          <select v-model.number="form.status">
            <option :value="1">启用</option>
            <option :value="0">停用</option>
          </select>
        </label>

        <div class="au-modal-actions">
          <button type="button" class="au-btn" @click="formOpen = false">取消</button>
          <button type="button" class="au-btn au-btn-primary" :disabled="saving" @click="submitForm">
            {{ saving ? '保存中…' : '保存' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 权限 -->
    <div v-if="permOpen" class="au-mask" @click.self="permOpen = false">
      <div class="au-modal">
        <h3>权限配置 · {{ permUser?.username }}</h3>
        <p class="au-hint">勾选该账号可访问的资源类型与动作。未勾选即无权访问。</p>

        <table class="au-perm">
          <thead>
            <tr>
              <th>资源</th>
              <th>查看</th>
              <th>修改</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in permissionCatalog" :key="item.type">
              <td>{{ item.label }}</td>
              <td><input type="checkbox" v-model="permDraft[item.type].read" /></td>
              <td><input type="checkbox" v-model="permDraft[item.type].write" /></td>
            </tr>
          </tbody>
        </table>

        <div class="au-modal-actions">
          <button type="button" class="au-btn" @click="permOpen = false">取消</button>
          <button type="button" class="au-btn au-btn-primary" :disabled="saving" @click="submitPermissions">
            {{ saving ? '保存中…' : '保存权限' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 重置密码 -->
    <div v-if="resetOpen" class="au-mask" @click.self="resetOpen = false">
      <div class="au-modal">
        <h3>重置密码 · {{ resetUser?.username }}</h3>
        <label class="au-field">
          <span>新密码</span>
          <input v-model="resetPassword" type="password" placeholder="至少 8 位，含字母与数字" />
        </label>
        <div class="au-modal-actions">
          <button type="button" class="au-btn" @click="resetOpen = false">取消</button>
          <button type="button" class="au-btn au-btn-primary" :disabled="saving" @click="submitReset">
            {{ saving ? '提交中…' : '确认重置' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import {
  createAdminUser,
  deleteAdminUser,
  getAdminUserPermissions,
  listAdminUsers,
  resetAdminUserPassword,
  setAdminUserPermissions,
  updateAdminUser
} from '../api/auth.js'

const props = defineProps({
  user: { type: Object, default: () => ({}) }
})

const users = ref([])
const loading = ref(false)
const saving = ref(false)
const notice = reactive({ text: '', type: 'info' })

const formOpen = ref(false)
const formMode = ref('create')
const editingId = ref(0)
const form = reactive({
  username: '',
  password: '',
  role: 'operator',
  email: '',
  accountLimit: null,
  status: 1
})

const permOpen = ref(false)
const permUser = ref(null)
const permDraft = reactive({})

const resetOpen = ref(false)
const resetUser = ref(null)
const resetPassword = ref('')

const permissionCatalog = [
  { type: 'goods', label: '商品' },
  { type: 'account', label: '闲鱼账号' },
  { type: 'delivery', label: '发货规则' },
  { type: 'reply', label: '自动回复' }
]

const currentName = computed(() => props.user?.username || '当前用户')
const currentRoleLabel = computed(() => roleLabel(props.user?.role))

function roleLabel(role) {
  if (role === 'super') return '超级管理员'
  if (role === 'viewer') return '只读'
  return '操作员'
}

function flash(text, type = 'success') {
  notice.text = text
  notice.type = type
  setTimeout(() => { notice.text = '' }, 3000)
}

function emptyPermDraft() {
  permissionCatalog.forEach(item => {
    permDraft[item.type] = { read: false, write: false }
  })
}

async function load() {
  loading.value = true
  try {
    const res = await listAdminUsers()
    users.value = (res?.data || []).filter(row => row.role !== 'super')
  } catch (err) {
    notice.text = err?.message || '加载失败'
    notice.type = 'error'
  } finally {
    loading.value = false
  }
}

function openCreate() {
  formMode.value = 'create'
  editingId.value = 0
  Object.assign(form, { username: '', password: '', role: 'operator', email: '', accountLimit: null, status: 1 })
  formOpen.value = true
}

function openEdit(row) {
  formMode.value = 'edit'
  editingId.value = row.id
  Object.assign(form, {
    username: row.username,
    password: '',
    role: row.role === 'super' ? 'operator' : row.role,
    email: row.email || '',
    accountLimit: row.accountLimit,
    status: row.status
  })
  formOpen.value = true
}

async function submitForm() {
  saving.value = true
  try {
    if (formMode.value === 'create') {
      await createAdminUser({
        username: form.username,
        password: form.password,
        role: form.role,
        email: form.email || null,
        accountLimit: form.accountLimit === '' || form.accountLimit === null ? null : Number(form.accountLimit)
      })
      flash('子账号已创建')
    } else {
      await updateAdminUser(editingId.value, {
        role: form.role,
        email: form.email || null,
        status: form.status,
        accountLimit: form.accountLimit === '' || form.accountLimit === null ? null : Number(form.accountLimit)
      })
      flash('已保存')
    }
    formOpen.value = false
    await load()
  } catch (err) {
    flash(err?.message || '保存失败', 'error')
  } finally {
    saving.value = false
  }
}

async function openPermissions(row) {
  permUser.value = row
  emptyPermDraft()
  permOpen.value = true
  try {
    const res = await getAdminUserPermissions(row.id)
    ;(res?.data || []).forEach(item => {
      const slot = permDraft[item.resource_type]
      if (!slot) return
      if (item.action === 'read') slot.read = true
      if (item.action === 'write') slot.write = true
    })
  } catch (err) {
    flash(err?.message || '读取权限失败', 'error')
  }
}

async function submitPermissions() {
  if (!permUser.value) return
  saving.value = true
  try {
    const permissions = []
    permissionCatalog.forEach(item => {
      const slot = permDraft[item.type]
      if (slot.read) permissions.push({ resourceType: item.type, resourceId: null, action: 'read' })
      if (slot.write) permissions.push({ resourceType: item.type, resourceId: null, action: 'write' })
    })
    await setAdminUserPermissions(permUser.value.id, permissions)
    flash('权限已保存')
    permOpen.value = false
  } catch (err) {
    flash(err?.message || '保存失败', 'error')
  } finally {
    saving.value = false
  }
}

function openReset(row) {
  resetUser.value = row
  resetPassword.value = ''
  resetOpen.value = true
}

async function submitReset() {
  if (!resetUser.value) return
  saving.value = true
  try {
    await resetAdminUserPassword(resetUser.value.id, resetPassword.value)
    flash('密码已重置')
    resetOpen.value = false
  } catch (err) {
    flash(err?.message || '重置失败', 'error')
  } finally {
    saving.value = false
  }
}

async function removeUser(row) {
  const ok = window.confirm(`确认删除子账号「${row.username}」？该操作不可撤销。`)
  if (!ok) return
  try {
    await deleteAdminUser(row.id)
    flash('已删除')
    await load()
  } catch (err) {
    flash(err?.message || '删除失败', 'error')
  }
}

onMounted(load)
</script>

<style scoped>
.au-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.au-notice {
  padding: 10px 14px;
  border-radius: 12px;
  font-size: 13px;
  border: 1px solid var(--line);
  background: #eef6ff;
  color: var(--primary);
}
.au-notice.error {
  background: #fff1f1;
  color: var(--red);
  border-color: #ffd9d9;
}

.au-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.au-head h2 {
  margin: 0 0 6px;
  font-size: 20px;
}
.au-head p {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}

.au-card {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  overflow: hidden;
}

.au-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.au-table th,
.au-table td {
  padding: 12px 16px;
  text-align: left;
  border-bottom: 1px solid var(--line);
  vertical-align: middle;
  /* 与 BaseTable 保持一致：单元格不换行，避免中文逐字竖排 */
  white-space: nowrap;
}
.au-table th {
  background: #f8fbff;
  color: var(--muted);
  font-weight: 500;
  white-space: nowrap;
}
.au-table tbody tr:last-child td {
  border-bottom: none;
}
.au-table .right {
  text-align: right;
}

.au-name {
  font-weight: 600;
}
.au-sub {
  color: var(--muted);
  font-size: 12px;
  margin-top: 2px;
}

.au-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  background: #eef2f9;
  color: var(--muted);
}
.au-tag.role-operator {
  background: #e8f1ff;
  color: var(--primary);
}
.au-tag.role-viewer {
  background: #f1f0ff;
  color: var(--purple);
}
.au-tag.is-on {
  background: #e8fbf1;
  color: var(--green);
}
.au-tag.is-off {
  background: #fff1f1;
  color: var(--red);
}

.au-btn {
  padding: 8px 16px;
  border-radius: 10px;
  border: 1px solid var(--line);
  background: #fff;
  color: var(--text);
  cursor: pointer;
  font-size: 13px;
}
.au-btn:disabled {
  opacity: .55;
  cursor: not-allowed;
}
.au-btn-primary {
  background: var(--primary);
  border-color: var(--primary);
  color: #fff;
}

.au-link {
  background: none;
  border: none;
  color: var(--primary);
  cursor: pointer;
  font-size: 13px;
  padding: 0 6px;
}
.au-link.danger {
  color: var(--red);
}
.au-link:disabled {
  color: var(--muted);
  cursor: not-allowed;
}

.au-empty {
  padding: 48px;
  text-align: center;
  color: var(--muted);
}

.au-mask {
  position: fixed;
  inset: 0;
  background: rgba(16, 28, 51, .38);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  z-index: 60;
}
.au-modal {
  width: 100%;
  max-width: 460px;
  background: var(--card);
  border-radius: var(--radius);
  padding: 22px;
  box-shadow: 0 24px 60px rgba(16, 28, 51, .22);
  max-height: 86vh;
  overflow-y: auto;
}
.au-modal h3 {
  margin: 0 0 16px;
  font-size: 17px;
}
.au-hint {
  margin: -8px 0 14px;
  font-size: 12px;
  color: var(--muted);
}

.au-field {
  display: block;
  margin-bottom: 14px;
}
.au-field span {
  display: block;
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 6px;
}
.au-field input,
.au-field select {
  width: 100%;
  padding: 9px 12px;
  border-radius: 10px;
  border: 1px solid var(--line);
  background: #fff;
  color: var(--text);
  font-size: 13px;
  outline: none;
}
.au-field input:focus,
.au-field select:focus {
  border-color: var(--primary);
}

.au-modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 20px;
}

.au-perm {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.au-perm th,
.au-perm td {
  padding: 8px 6px;
  border-bottom: 1px solid var(--line);
  text-align: center;
}
.au-perm th:first-child,
.au-perm td:first-child {
  text-align: left;
}

@media (max-width: 720px) {
  .au-table th:nth-child(4),
  .au-table td:nth-child(4),
  .au-table th:nth-child(5),
  .au-table td:nth-child(5) {
    display: none;
  }
  .au-head {
    flex-direction: column;
  }
}
</style>
