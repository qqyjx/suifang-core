/**
 * 体验版 / 正式版 单一开关
 *
 * 切换正式版打包流程：
 *   1. 把 IS_TEST_BUILD 改 false
 *   2. 更新 BUILD_TAG（如 'prod-1.0.0'）
 *   3. 微信开发者工具 → 上传 → 提交审核（需先做隐私协议页和审核材料）
 *
 * 影响：
 *   - 首页右上角 "测试版" 角标在 IS_TEST_BUILD=true 时显示
 *   - vConsole 在 IS_TEST_BUILD=true 时自动开启（app.ts onLaunch）
 *   - SUPPORTED_DEVICE_PREFIXES / MIN_BLE_RSSI 是 BLE 扫描过滤参数
 *   - API_BASE 是后端服务器地址
 */
export const ENV = {
  IS_TEST_BUILD: true,
  BUILD_TAG: '5.06-v11',
  API_BASE: 'https://dc.ncrc.org.cn/api2',
  SUPPORTED_DEVICE_PREFIXES: ['VP-', 'S101', 'VPR'],
  MIN_BLE_RSSI: -75,
};
