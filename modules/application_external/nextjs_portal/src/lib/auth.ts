import type { NextAuthOptions } from 'next-auth'
import CredentialsProvider from 'next-auth/providers/credentials'
import { users } from './data'

export const authOptions: NextAuthOptions = {
  providers: [
    CredentialsProvider({
      name: 'credentials',
      credentials: {
        username: { label: 'Username', type: 'text' },
        password: { label: 'Password', type: 'password' }
      },
      async authorize(credentials) {
        if (!credentials?.username || !credentials?.password) return null
        const user = users.find(
          u => u.username === credentials.username && u.password === credentials.password
        )
        if (!user) return null
        return { id: String(user.id), name: user.name, email: user.email }
      }
    })
  ],
  session: { strategy: 'jwt' }
}
