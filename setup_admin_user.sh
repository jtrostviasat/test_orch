#!/bin/bash
# Create a local admin user and session token for testing without LDAP.
# Usage: ./setup_admin_user.sh [username] [token]
# Defaults: username=admin, token=DEVTOKEN

USERNAME=${1:-admin}
TOKEN=${2:-DEVTOKEN}

docker compose exec -T postgres psql -U test_orch -d test_orch <<SQL
INSERT INTO users (username, display_name, email, created_at)
VALUES ('$USERNAME', 'Local Admin', '${USERNAME}@localhost', now())
ON CONFLICT (username) DO NOTHING;

INSERT INTO user_sessions (token, user_id, created_at, expires_at)
SELECT '$TOKEN', id, now(), now() + interval '24 hours'
FROM users WHERE username = '$USERNAME'
ON CONFLICT (token) DO NOTHING;
SQL

echo ""
echo "✓ Admin user created:"
echo "  Username: $USERNAME"
echo "  Session token: $TOKEN"
echo ""
echo "To log in:"
echo "  1. Open http://localhost:8000/admin"
echo "  2. DevTools → Application → Cookies → Add cookie:"
echo "     Name: session_token"
echo "     Value: $TOKEN"
echo "  3. Reload page"
