import './style.css'

const status = document.querySelector('#status')

try {
  const response = await fetch('http://127.0.0.1:8000/health')
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  const data = await response.json()
  status.textContent = `Сервис доступен: ${data.status}`
  status.classList.add('healthy')
} catch {
  status.textContent = 'Backend недоступен. Запустите FastAPI на порту 8000.'
  status.classList.add('unhealthy')
}
