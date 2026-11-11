# AndroidDeviceWorker（安卓设备执行器）

M2 V0.1 复用 Google ADB（Android Debug Bridge，安卓调试桥）作为 borrowed runtime（复用运行时）。

当前只读能力：

- `android_device_info（安卓设备信息）`
- `android_screenshot（安卓截图）`
- `read_only_device_evidence（只读设备证据）`

当前不提供点击、输入、安装、卸载、授权或发布动作。连接真机时需要用户在设备上自行确认 USB debugging authorization（USB 调试授权）。
