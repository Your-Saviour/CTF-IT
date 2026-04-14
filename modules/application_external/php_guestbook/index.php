<?php
$messagesFile = '/opt/guestbook/messages.txt';

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $name    = trim($_POST['name'] ?? '');
    $message = trim($_POST['message'] ?? '');
    if ($name !== '' && $message !== '') {
        file_put_contents($messagesFile, $name . ': ' . $message . "\n", FILE_APPEND | LOCK_EX);
    }
    header('Location: /');
    exit;
}

$messages = [];
if (file_exists($messagesFile)) {
    $messages = array_filter(array_map('trim', file($messagesFile)));
}
?>
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Guestbook</title>
</head>
<body>
  <h1>Guestbook</h1>

  <h2>Leave a Message</h2>
  <form method="post" action="/">
    <label>Name: <input type="text" name="name" required></label><br>
    <label>Message: <textarea name="message" required></textarea></label><br>
    <button type="submit">Post</button>
  </form>

  <h2>Messages</h2>
  <?php if (empty($messages)): ?>
    <p>No messages yet.</p>
  <?php else: ?>
    <ul>
      <?php foreach ($messages as $msg): ?>
        <li><?= htmlspecialchars($msg, ENT_QUOTES, 'UTF-8') ?></li>
      <?php endforeach; ?>
    </ul>
  <?php endif; ?>
</body>
</html>
