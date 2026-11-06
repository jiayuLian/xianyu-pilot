import request from '../utils/request.js'
import { clearAuth } from '../utils/auth.js'

// Login owns an inline, focus-adjacent error state. Suppress the global status
// banner for this request so one failure is not announced twice to users and
// assistive technology.
export const login = data => request.post('/auth/login', data, { suppressGlobalError: true })

export const getProfile = () => request.get('/auth/profile')

export async function logout() {
  try {
    return await request.post('/auth/logout', {})
  } finally {
    clearAuth()
  }
}

// ---- 子账号管理（仅 role=super 可调用写接口）----

export const listAdminUsers = () => request.get('/auth/users')

export const createAdminUser = data => request.post('/auth/users', data)

export const updateAdminUser = (id, data) => request.put(`/auth/users/${id}`, data)

export const deleteAdminUser = id => request.delete(`/auth/users/${id}`)

export const resetAdminUserPassword = (id, newPassword) =>
  request.put(`/auth/users/${id}/password`, { newPassword })

export const getAdminUserPermissions = id => request.get(`/auth/users/${id}/permissions`)

export const setAdminUserPermissions = (id, permissions) =>
  request.put(`/auth/users/${id}/permissions`, { permissions })

export const getMyProfileDetail = () => request.get('/auth/users/me/profile')

export const getMyPermissions = () => request.get('/auth/users/me/permissions')
