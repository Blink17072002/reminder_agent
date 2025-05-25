document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.toggle-password').forEach(btn => {
    btn.addEventListener('click', () => {
      const targetId = btn.dataset.target;
      const showIcon = btn.dataset.showIcon;
      const hideIcon = btn.dataset.hideIcon;
      const pwdField = document.getElementById(targetId);
      const icon = btn.querySelector('img');

      if (pwdField.type === 'password') {
        pwdField.type = 'text';
        icon.src = showIcon;
      } else {
        pwdField.type = 'password';
        icon.src = hideIcon;
      }
    });
  });
});