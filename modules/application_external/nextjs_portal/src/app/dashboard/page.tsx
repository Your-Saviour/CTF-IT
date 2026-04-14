import { getServerSession } from 'next-auth'
import { authOptions } from '@/lib/auth'
import { redirect } from 'next/navigation'

export default async function Dashboard() {
  const session = await getServerSession(authOptions)
  if (!session) redirect('/')

  return (
    <main>
      <h1>Employee Dashboard</h1>
      <p>Welcome, {session.user?.name}.</p>
      <p>This is the internal employee portal dashboard.</p>
      <p><a href="/api/auth/signout">Sign out</a></p>
    </main>
  )
}
